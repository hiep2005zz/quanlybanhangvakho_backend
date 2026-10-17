# backend/app/models/__init__.py
# Ensure all models are registered with Base metadata in correct order
from app.models.supplier import SupplierEntity
from app.models.entities import (
    UserEntity,
    ProductEntity,
    DealerEntity,
    InventoryTransactionEntity,
    OrderEntity,
    OrderItemEntity,
    DealerDeliveryPointEntity,
    MasterDeliveryPointEntity,
    CategoryEntity,
    AuditLogEntity,
    WarehouseStockEntity,
    StockAuditEntity,
    StockTransferEntity,
)
from app.models.goods_receipt import (
    WarehouseEntity,
    UnitOfMeasureEntity,
    GoodsReceiptNoteEntity,
    GoodsReceiptNoteItemEntity,
    ProductBatchEntity,
    InventoryLedgerEntity,
)
from app.models.warehouse import (
    WarehouseLocationEntity,
    LocationStockEntity,
    WarehouseZoneEntity,
    WarehouseRackEntity,
)
from app.models.price_book import PriceBookEntity, PriceBookItemEntity
from app.models.discount import DiscountPolicyEntity, DiscountTierEntity
from app.models.return_order import ReturnOrderEntity, ReturnOrderItemEntity
