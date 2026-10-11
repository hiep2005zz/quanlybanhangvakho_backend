# backend/app/services/pricing_service.py
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.models.price_book import PriceBookEntity, PriceBookItemEntity
from app.models.entities import ProductEntity, DealerEntity
from app.models.discount import DiscountPolicyEntity, DiscountTierEntity


class PricingService:
    @staticmethod
    def normalize_customer_group(customer_group: Optional[str]) -> list[str]:
        group = customer_group or "dai_ly_cap_1"
        if group in ["CAP_1", "Dai_ly_cap_1", "Đại lý cấp 1", "dai_ly_cap_1"]:
            return ["Dai_ly_cap_1", "CAP_1", "Đại lý cấp 1", "dai_ly_cap_1"]
        elif group in ["CAP_2", "Dai_ly_cap_2", "Đại lý cấp 2", "dai_ly_cap_2"]:
            return ["Dai_ly_cap_2", "CAP_2", "Đại lý cấp 2", "dai_ly_cap_2"]
        elif group in ["RETAIL", "Khach_le", "Khách lẻ", "khach_le"]:
            return ["Khach_le", "RETAIL", "Khách lẻ", "khach_le"]
        return [group]

    @staticmethod
    def resolve_customer_group_from_dealer(dealer_id_or_group: Any, db: Session) -> str:
        if isinstance(dealer_id_or_group, int) or (isinstance(dealer_id_or_group, str) and dealer_id_or_group.isdigit()):
            d_id = int(dealer_id_or_group)
            dealer = db.query(DealerEntity).filter(DealerEntity.id == d_id).first()
            if not dealer:
                from app.models.dealer import DEALERS_DB
                dealer = DEALERS_DB.get(d_id)
            if dealer:
                return getattr(dealer, "customer_group", None) or "dai_ly_cap_1"
        elif isinstance(dealer_id_or_group, str) and dealer_id_or_group:
            return dealer_id_or_group
        return "dai_ly_cap_1"

    @staticmethod
    def get_effective_price(
        customer_group_or_dealer_id: Any,
        product_id: int,
        db: Session
    ) -> Dict[str, Any]:
        """
        AC1: Tra cứu đơn giá và giá sàn theo Bảng giá hiệu lực.
        Điều kiện bảng giá: status == 'ACTIVE' VÀ valid_from <= TODAY <= valid_to.
        Chặn khi thiếu bảng giá (Hard Validation):
        Nếu SKU chưa có bảng giá hiệu lực: Raise HTTPException(400, "SKU [Mã_SKU] chưa có bảng giá đang hiệu lực cho nhóm khách hàng này. Vui lòng kiểm tra lại cấu hình giá.")
        """
        customer_group = PricingService.resolve_customer_group_from_dealer(customer_group_or_dealer_id, db)
        aliases = PricingService.normalize_customer_group(customer_group)
        now = datetime.now(timezone.utc)

        prod = db.query(ProductEntity).filter(ProductEntity.id == product_id).first()
        prod_code = prod.code if prod else f"SP{product_id:03d}"
        prod_name = prod.name if prod else f"Sản phẩm #{product_id}"

        # Tìm bảng giá hiệu lực và dòng sản phẩm
        item_match = db.query(PriceBookItemEntity, PriceBookEntity).join(
            PriceBookEntity, PriceBookItemEntity.price_book_id == PriceBookEntity.id
        ).filter(
            PriceBookEntity.customer_group.in_(aliases),
            PriceBookEntity.status == "ACTIVE",
            PriceBookEntity.valid_from <= now,
            PriceBookEntity.valid_to >= now,
            PriceBookItemEntity.product_id == product_id
        ).order_by(PriceBookEntity.version.desc(), PriceBookEntity.created_at.desc()).first()

        if not item_match:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SKU [{prod_code}] chưa có bảng giá đang hiệu lực cho nhóm khách hàng này. Vui lòng kiểm tra lại cấu hình giá."
            )

        pb_item, pb = item_match
        sale_val = pb_item.sale_price if pb_item.sale_price is not None else pb_item.price
        floor_val = pb_item.floor_price if pb_item.floor_price is not None else pb_item.min_price

        # Fallback về giá niêm yết của sản phẩm chỉ khi giá bảng giá chưa được đặt (bằng 0 hoặc None)
        if (sale_val is None or sale_val == 0) and prod and prod.sell_price:
            sale_val = prod.sell_price
        if (floor_val is None or floor_val == 0) and prod and prod.floor_price:
            floor_val = prod.floor_price

        return {
            "price_book_id": pb.id,
            "price_book_code": pb.code,
            "price_book_name": pb.name,
            "customer_group": pb.customer_group,
            "product_id": product_id,
            "product_code": prod_code,
            "product_name": prod_name,
            "sale_price": float(sale_val or 0.0),
            "floor_price": float(floor_val or 0.0),
            "unit_price": float(sale_val or 0.0),
        }

    @staticmethod
    def compute_tier_discount(
        product_id: int,
        quantity: int,
        customer_group: Optional[str] = None,
        base_price: float = 0.0,
        db: Optional[Session] = None
    ) -> Dict[str, Any]:
        """
        AC2: Tự động tính Chiết khấu theo sản lượng (Tiered Quantity Discount).
        Map với các bậc sản lượng quy định: min_qty <= quantity < max_qty (hoặc <= max_qty nếu là khoảng đóng).
        Nếu quantity dưới ngưỡng tối thiểu của bậc thấp nhất: Chiết khấu mặc định là 0.
        """
        if quantity <= 0:
            return {
                "discount_rate": 0.0,
                "discount_amount": 0.0,
                "unit_discount": 0.0,
                "applied_policy_code": None,
                "applied_policy_name": None,
                "applied_tier_label": None,
                "line_total": 0.0,
            }

        policies = []
        if db:
            entities = db.query(DiscountPolicyEntity).filter(
                DiscountPolicyEntity.is_active == True,
                DiscountPolicyEntity.status == "active"
            ).all()
            policies = [e.to_dict() for e in entities]

        if not policies:
            from app.api.v1.endpoints.discounts import load_discount_policies
            policies = load_discount_policies(db)

        best_rate = 0.0
        best_amount = 0.0
        applied_code = None
        applied_name = None
        applied_label = None

        prod = db.query(ProductEntity).filter(ProductEntity.id == product_id).first() if db else None
        prod_cat = prod.category if prod else None

        for policy in policies:
            if not policy.get("is_active"):
                continue

            start_date = policy.get("start_date")
            end_date = policy.get("end_date")
            today_str = datetime.now().strftime("%Y-%m-%d")
            if start_date and today_str < str(start_date):
                continue
            if end_date and today_str > str(end_date):
                continue

            p_cat = (policy.get("category") or "ALL").upper()
            if p_cat != "ALL" and prod_cat and p_cat != prod_cat.upper():
                continue

            t_grp = (policy.get("target_dealer_type") or policy.get("target_group") or "ALL").upper()
            if t_grp != "ALL" and customer_group:
                c_norm = customer_group.upper()
                if t_grp not in c_norm and c_norm not in t_grp:
                    continue

            tiers = policy.get("tiers", [])
            for tier in tiers:
                min_q = tier.get("min_quantity", 0)
                max_q = tier.get("max_quantity")
                disc_pct = float(tier.get("discount_percent", 0.0) or 0.0)
                disc_fixed = float(tier.get("discount_amount", 0.0) or 0.0)

                # Kiểm tra bậc sản lượng: min_qty <= quantity
                in_tier = False
                if max_q is not None and max_q > 0:
                    in_tier = (min_q <= quantity <= max_q)
                else:
                    in_tier = (quantity >= min_q)

                if in_tier and min_q > 0:
                    curr_rate = disc_pct
                    curr_amt = (quantity * base_price * (disc_pct / 100.0)) + (disc_fixed * quantity)
                    if curr_amt > best_amount:
                        best_amount = curr_amt
                        best_rate = curr_rate
                        applied_code = policy.get("code")
                        applied_name = policy.get("name")
                        applied_label = f"Từ {min_q} {f'đến {max_q}' if max_q else 'trở lên'}: giảm {disc_pct}%"

        line_subtotal = quantity * base_price
        line_discount = round(best_amount, 2)
        line_total = round(max(0.0, line_subtotal - line_discount), 2)

        return {
            "discount_rate": best_rate,
            "discount_amount": line_discount,
            "unit_discount": round(line_discount / quantity, 2) if quantity > 0 else 0.0,
            "applied_policy_code": applied_code,
            "applied_policy_name": applied_name,
            "applied_tier_label": applied_label,
            "line_total": line_total,
        }

    @staticmethod
    def calculate_line_item(
        customer_group_or_dealer_id: Any,
        product_id: int,
        quantity: int,
        override_price: Optional[float] = None,
        db: Session = None
    ) -> Dict[str, Any]:
        """
        AC1, AC2, AC3: Tổng hợp tính toán cho một dòng hàng:
        1. Lấy đơn giá theo bảng giá hiệu lực (AC1)
        2. Kiểm tra override_price & giá sàn floor_price (AC3)
        3. Tính chiết khấu theo sản lượng (AC2)
        """
        price_info = PricingService.get_effective_price(customer_group_or_dealer_id, product_id, db)
        book_price = price_info["sale_price"]
        floor_price = price_info["floor_price"]

        unit_price = float(override_price) if (override_price is not None and override_price >= 0) else book_price

        # Kiểm tra giá sàn (AC3)
        is_below_floor = bool(floor_price > 0 and unit_price < floor_price)
        approval_reason = None
        if is_below_floor:
            approval_reason = f"SKU [{price_info['product_code']}] có đơn giá bán nhỏ hơn giá sàn quy định ({floor_price:,.0f})."

        # Tính chiết khấu theo sản lượng (AC2)
        tier_calc = PricingService.compute_tier_discount(
            product_id=product_id,
            quantity=quantity,
            customer_group=price_info.get("customer_group"),
            base_price=unit_price,
            db=db
        )

        return {
            "product_id": product_id,
            "product_code": price_info["product_code"],
            "product_name": price_info["product_name"],
            "quantity": quantity,
            "unit_price": unit_price,
            "book_price": book_price,
            "floor_price": floor_price,
            "is_below_floor": is_below_floor,
            "requires_approval": is_below_floor,
            "approval_reason": approval_reason,
            "discount_rate": tier_calc["discount_rate"],
            "discount_amount": tier_calc["discount_amount"],
            "applied_policy_code": tier_calc["applied_policy_code"],
            "applied_policy_name": tier_calc["applied_policy_name"],
            "line_total": tier_calc["line_total"],
        }

    @staticmethod
    def validate_order_price_floor(
        items: List[Dict[str, Any]],
        customer_group_or_dealer_id: Any,
        db: Session
    ) -> Tuple[bool, List[str], List[Dict[str, Any]]]:
        """
        AC3: Kiểm tra giá sàn cho danh sách dòng hàng của đơn hàng.
        """
        requires_approval = False
        reasons = []
        evaluations = []

        for item in items:
            p_id = item.get("product_id")
            actual_price = float(item.get("price") if item.get("price") is not None else (item.get("unit_price") or 0.0))
            qty = int(item.get("quantity") or 1)

            try:
                price_info = PricingService.get_effective_price(customer_group_or_dealer_id, p_id, db)
                floor_price = price_info["floor_price"]
                prod_code = price_info["product_code"]
                book_price = price_info["sale_price"]

                is_below = bool(floor_price > 0 and actual_price < floor_price)
                if is_below:
                    requires_approval = True
                    reason = f"SKU [{prod_code}] có đơn giá bán nhỏ hơn giá sàn quy định ({floor_price:,.0f})."
                    reasons.append(reason)

                evaluations.append({
                    "product_id": p_id,
                    "product_code": prod_code,
                    "product_name": price_info["product_name"],
                    "unit_price": actual_price,
                    "book_price": book_price,
                    "floor_price": floor_price,
                    "is_below_floor": is_below,
                    "quantity": qty,
                })
            except HTTPException as e:
                requires_approval = True
                reasons.append(e.detail)
                evaluations.append({
                    "product_id": p_id,
                    "unit_price": actual_price,
                    "floor_price": 0.0,
                    "is_below_floor": True,
                    "quantity": qty,
                    "error": e.detail,
                })

        return requires_approval, reasons, evaluations
