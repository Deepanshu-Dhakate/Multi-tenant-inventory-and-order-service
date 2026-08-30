# Multi-Tenant Inventory & Order Service

A lightweight multi-tenant inventory and order service built with FastAPI, SQLAlchemy, and SQLite.

---

## Overview

This service provides warehouse stock tracking, order querying, inventory adjustments, and stock reservations across isolated tenant scopes.

### Key Implementations

* **Multi-Tenancy**: Data isolation across requests using the `X-Tenant-Id` header.
* **Stock Reservation (`POST /orders/{order_id}/reserve`)**: Atomically reserves all order line items against warehouse stock using SQLite `BEGIN IMMEDIATE` locks. 
* **Idempotency**: Retried reservation calls for already-reserved orders return the current state without duplicate deductions.
* **Race Condition Prevention**: Enforces transaction locks on stock adjustments and reservations to eliminate overselling during high concurrency.
* **Query Optimization**: Eager loading (`selectinload`) and indexing applied to resolve slow list endpoint queries.

---

## Quickstart

### 1. Requirements
* Python 3.9+

### 2. Setup & Installation
# Install dependencies
pip install fastapi uvicorn sqlalchemy pydantic
# or: pip install -r requirements.txt
python app.py
