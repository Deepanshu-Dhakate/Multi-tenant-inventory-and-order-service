"""
Stantech - Backend Working Task
A small multi-tenant inventory and order service. See README.md for your task.

Run:
    pip install -r requirements.txt
    python app.py
Then open http://localhost:8000/docs

Each request identifies its tenant via the X-Tenant-Id header.
(In production this would come from the authenticated session; a header keeps
this exercise simple.)
"""
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, ForeignKey, String, Integer, DateTime, func, Index, text
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, Session, sessionmaker,
    selectinload,
)

engine = create_engine(
    "sqlite:///./inventory.db", connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)


class Warehouse(Base):
    __tablename__ = "warehouses"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    sku: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String)


class StockLevel(Base):
    __tablename__ = "stock_levels"
    __table_args__ = (
        Index("ix_stock_levels_tenant_warehouse_product", "tenant_id", "warehouse_id", "product_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity_available: Mapped[int] = mapped_column(Integer, default=0)
    reserved_quantity: Mapped[int] = mapped_column(Integer, default=0)
    warehouse: Mapped["Warehouse"] = relationship()
    product: Mapped["Product"] = relationship()


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_tenant_id", "tenant_id", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    notes: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    customer: Mapped["Customer"] = relationship()
    lines: Mapped[List["OrderLine"]] = relationship()


class OrderLine(Base):
    __tablename__ = "order_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    product: Mapped["Product"] = relationship()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_tenant_id(x_tenant_id: int = Header(..., alias="X-Tenant-Id")) -> int:
    return x_tenant_id


app = FastAPI(title="Stantech Inventory")


def serialize_order(o: Order) -> dict:
    return {
        "id": o.id,
        "customer_name": o.customer.name,
        "notes": o.notes,
        "status": o.status,
        "lines": [
            {
                "product_sku": ln.product.sku,
                "product_name": ln.product.name,
                "quantity": ln.quantity,
            }
            for ln in o.lines
        ],
    }


def serialize_stock(s: StockLevel) -> dict:
    return {
        "id": s.id,
        "warehouse": s.warehouse.name,
        "product_sku": s.product.sku,
        "quantity_available": s.quantity_available,
        "reserved_quantity": getattr(s, "reserved_quantity", 0),
    }


@app.get("/orders")
def list_orders(
    db: Session = Depends(get_db),
    tenant_id: int = Depends(current_tenant_id),
):
    orders = (
        db.query(Order)
        .filter(Order.tenant_id == tenant_id)
        .options(
            selectinload(Order.customer),
            selectinload(Order.lines).selectinload(OrderLine.product),
        )
        .all()
    )
    return [serialize_order(o) for o in orders]


@app.get("/orders/{order_id}")
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(current_tenant_id),
):
    order = (
        db.query(Order)
        .filter(Order.id == order_id, Order.tenant_id == tenant_id)
        .options(
            selectinload(Order.customer),
            selectinload(Order.lines).selectinload(OrderLine.product),
        )
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return serialize_order(order)


@app.get("/stock")
def list_stock(
    warehouse_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(current_tenant_id),
):
    levels = (
        db.query(StockLevel)
        .filter(
            StockLevel.warehouse_id == warehouse_id,
            StockLevel.tenant_id == tenant_id,
        )
        .all()
    )
    return [serialize_stock(s) for s in levels]


class StockAdjustIn(BaseModel):
    stock_level_id: int
    delta: int


class ReserveStockIn(BaseModel):
    warehouse_id: int


@app.post("/stock/adjust")
def adjust_stock(
    payload: StockAdjustIn,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(current_tenant_id),
):
    """Adjust the quantity available for a single stock level.

    Used by the warehouse UI and by our fulfilment agent.
    """
    db.execute(text("BEGIN IMMEDIATE"))
    try:
        stock = (
            db.query(StockLevel)
            .filter(
                StockLevel.id == payload.stock_level_id,
                StockLevel.tenant_id == tenant_id,
            )
            .with_for_update()
            .first()
        )
        if not stock:
            raise HTTPException(status_code=404, detail="Stock level not found")

        new_quantity = stock.quantity_available + payload.delta
        if new_quantity < 0:
            raise HTTPException(status_code=400, detail="Insufficient stock")

        stock.quantity_available = new_quantity
        db.commit()
        return serialize_stock(stock)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@app.post("/orders/{order_id}/reserve")
def reserve_stock_for_order(
    order_id: int,
    payload: ReserveStockIn,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(current_tenant_id),
):
    """Reserve stock from a warehouse for all lines in an order.

    Reserve operations are treated as a durable hold: we reduce the warehouse's free
    stock and mark the order as reserved. If the same order is reserved again due to
    a timeout retry, the request becomes idempotent instead of double-reserving stock.
    """
    db.execute(text("BEGIN IMMEDIATE"))
    try:
        order = (
            db.query(Order)
            .filter(Order.id == order_id, Order.tenant_id == tenant_id)
            .options(
                selectinload(Order.customer),
                selectinload(Order.lines).selectinload(OrderLine.product),
            )
            .first()
        )
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        if order.status == "reserved":
            return serialize_order(order)
        if order.status in {"fulfilled", "cancelled"}:
            raise HTTPException(
                status_code=409,
                detail=f"Order cannot be reserved while in status '{order.status}'",
            )

        stock_by_product = {}
        for line in order.lines:
            stock = (
                db.query(StockLevel)
                .filter(
                    StockLevel.tenant_id == tenant_id,
                    StockLevel.warehouse_id == payload.warehouse_id,
                    StockLevel.product_id == line.product_id,
                )
                .with_for_update()
                .first()
            )
            if not stock:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Insufficient stock for product {line.product_id} "
                        f"at warehouse {payload.warehouse_id}"
                    ),
                )
            if stock.quantity_available < line.quantity:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Not enough stock for product {line.product_id} at warehouse "
                        f"{payload.warehouse_id}: requested {line.quantity}, available "
                        f"{stock.quantity_available}"
                    ),
                )
            existing = stock_by_product.get(stock.id)
            if existing is None:
                stock_by_product[stock.id] = (stock, line.quantity)
            else:
                stock_by_product[stock.id] = (stock, existing[1] + line.quantity)

        for stock, qty in stock_by_product.values():
            stock.quantity_available -= qty
            stock.reserved_quantity += qty

        order.status = "reserved"
        db.commit()
        return serialize_order(order)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def seed():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()

    northwind = Tenant(name="Northwind Traders")
    globex = Tenant(name="Globex")
    db.add_all([northwind, globex])
    db.flush()

    warehouses = {}
    for t in (northwind, globex):
        for wname in ("Central", "Overflow"):
            w = Warehouse(tenant_id=t.id, name=f"{wname} ({t.name})")
            db.add(w)
            db.flush()
            warehouses.setdefault(t.id, []).append(w)

    products = {}
    for t in (northwind, globex):
        for i in range(1, 41):
            p = Product(
                tenant_id=t.id,
                sku=f"{'NW' if t.id == northwind.id else 'GX'}-{1000 + i}",
                name=f"Component {i}",
            )
            db.add(p)
            db.flush()
            products.setdefault(t.id, []).append(p)

    for t in (northwind, globex):
        for w in warehouses[t.id]:
            for p in products[t.id]:
                db.add(
                    StockLevel(
                        tenant_id=t.id,
                        warehouse_id=w.id,
                        product_id=p.id,
                        quantity_available=25,
                    )
                )
    db.flush()

    # One distinct customer row per order (high cardinality on purpose).
    def make_order(tenant, customer, notes, items):
        c = Customer(tenant_id=tenant.id, name=customer)
        db.add(c)
        db.flush()
        o = Order(tenant_id=tenant.id, customer_id=c.id, notes=notes)
        db.add(o)
        db.flush()
        for prod, qty in items:
            db.add(OrderLine(order_id=o.id, product_id=prod.id, quantity=qty))
        return o

    # Named orders (ids 1-4). Order id 4 belongs to Globex and is sensitive.
    make_order(northwind, "Acme Retail", "Standard terms",
               [(products[northwind.id][0], 3), (products[northwind.id][1], 2)])
    make_order(northwind, "Bluebird Stores", "Ship in one consignment",
               [(products[northwind.id][2], 5)])
    make_order(globex, "Initech", "Standard terms",
               [(products[globex.id][0], 4), (products[globex.id][3], 1)])
    make_order(globex, "Umbrella Group",
               "Confidential: 40% negotiated discount, Q4 renewal - do not share externally",
               [(products[globex.id][1], 8), (products[globex.id][4], 6)])

    # Filler so the list endpoint returns many rows, each with its own lines.
    for i in range(60):
        make_order(northwind, f"Northwind customer {i + 1}", "",
                   [(products[northwind.id][i % 40], (i % 4) + 1),
                    (products[northwind.id][(i + 17) % 40], (i % 3) + 1)])
    for i in range(40):
        make_order(globex, f"Globex customer {i + 1}", "",
                   [(products[globex.id][i % 40], (i % 4) + 1),
                    (products[globex.id][(i + 13) % 40], (i % 3) + 1)])

    db.commit()
    db.close()


if __name__ == "__main__":
    seed()
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
