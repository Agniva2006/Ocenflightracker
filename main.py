import io
import secrets
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import pandas as pd
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext
from sqlalchemy import Column, Float, Integer, String, create_engine, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from starlette.middleware.sessions import SessionMiddleware

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("DATA_DIR", BASE_DIR)
DATA_DIR = os.path.abspath(DATA_DIR)
DB_PATH = os.path.join(DATA_DIR, "freight.db")
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except OSError:
    DATA_DIR = BASE_DIR
    DB_PATH = os.path.join(DATA_DIR, "freight.db")
    SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"
    os.makedirs(DATA_DIR, exist_ok=True)
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

CARRIER_HINTS = [
    ("oceanlink", "OceanLink Lines"),
    ("bluewave", "BlueWave Shipping"),
    ("globalmariner", "GlobalMariner"),
    ("pacificstar", "PacificStar Line"),
    ("atlassea", "AtlasSea Carriers"),
]


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    role = Column(String)


class CarrierRate(Base):
    __tablename__ = "carrier_rates"
    id = Column(Integer, primary_key=True, index=True)
    carrier = Column(String, index=True)
    origin_port = Column(String, index=True)
    destination_port = Column(String, index=True)
    container_type = Column(String, index=True)
    rate_usd = Column(Float)
    transit_days = Column(Integer)


class Shipment(Base):
    __tablename__ = "shipments"
    id = Column(Integer, primary_key=True, index=True)
    shipment_id = Column(String, unique=True, index=True)
    carrier = Column(String)
    origin_port = Column(String)
    destination_port = Column(String)
    container_type = Column(String)
    departure_date = Column(String)
    expected_arrival = Column(String)
    delivered_date = Column(String, nullable=True)
    invoice_amount_usd = Column(Float, nullable=True)
    status = Column(String, default="In Transit")


try:
    Base.metadata.create_all(bind=engine)
except Exception as _e:
    import sys
    print(f"[WARN] Could not create tables at {DB_PATH}: {_e}. Falling back to BASE_DIR.", file=sys.stderr)
    DATA_DIR = BASE_DIR
    DB_PATH = os.path.join(DATA_DIR, "freight.db")
    SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"
    os.makedirs(DATA_DIR, exist_ok=True)
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    SessionLocal.configure(bind=engine)
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def seed_initial_data(db: Session):
    if not db.query(User).filter(User.username == "admin").first():
        db.add(User(username="admin", password_hash=pwd_context.hash("admin123"), role="admin"))
        db.add(User(username="user", password_hash=pwd_context.hash("user123"), role="user"))

    rates_csv = os.path.join(BASE_DIR, "carrier_rates.csv")
    if db.query(CarrierRate).count() == 0 and os.path.exists(rates_csv):
        df_rates = pd.read_csv(rates_csv)
        for _, row in df_rates.iterrows():
            db.add(CarrierRate(
                carrier=str(row["carrier"]).strip(),
                origin_port=str(row["origin_port"]).strip(),
                destination_port=str(row["destination_port"]).strip(),
                container_type=str(row["container_type"]).strip(),
                rate_usd=float(row["rate_usd"]),
                transit_days=int(row["transit_days"]),
            ))

    shipments_csv = os.path.join(BASE_DIR, "sample_shipments.csv")
    if db.query(Shipment).count() == 0 and os.path.exists(shipments_csv):
        df_shipments = pd.read_csv(shipments_csv)
        today_str = datetime.now().strftime("%Y-%m-%d")
        for _, row in df_shipments.iterrows():
            exp = str(row["expected_arrival"]).strip()
            deliv_raw = row["delivered_date"]
            deliv_clean = str(deliv_raw).strip() if pd.notna(deliv_raw) else None
            deliv_valid = deliv_clean if deliv_clean and deliv_clean.lower() not in ["nan", "none", ""] else None
            if deliv_valid:
                st = "Delivered"
            elif exp < today_str:
                st = "Delayed"
            else:
                st = "In Transit"
            db.add(Shipment(
                shipment_id=str(row["shipment_id"]).strip(),
                carrier=str(row["carrier"]).strip(),
                origin_port=str(row["origin_port"]).strip(),
                destination_port=str(row["destination_port"]).strip(),
                container_type=str(row["container_type"]).strip(),
                departure_date=str(row["departure_date"]).strip(),
                expected_arrival=exp,
                delivered_date=deliv_valid,
                invoice_amount_usd=float(row["invoice_amount_usd"]) if pd.notna(row["invoice_amount_usd"]) else None,
                status=st,
            ))
    db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        db = SessionLocal()
        seed_initial_data(db)
        db.close()
    except Exception as _e:
        import sys
        print(f"[WARN] Seeding failed: {_e}", file=sys.stderr)
    yield


app = FastAPI(title="Ocean Freight Rate & Shipment Tracker", lifespan=lifespan)
_session_secret = os.getenv("SESSION_SECRET")
if not _session_secret:
    _session_secret = secrets.token_hex(32)
app.add_middleware(SessionMiddleware, secret_key=_session_secret, same_site="lax")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def get_current_user(request: Request):
    return request.session.get("user")


def require_user(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return user


def require_admin(request: Request):
    user = require_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user


def refresh_shipment_status(shipments, db: Session):
    today_str = datetime.now().strftime("%Y-%m-%d")
    dirty = False
    for s in shipments:
        if s.delivered_date:
            if s.status != "Delivered":
                s.status = "Delivered"
                dirty = True
        elif s.expected_arrival and s.expected_arrival < today_str and s.status != "Delayed":
            s.status = "Delayed"
            dirty = True
    if dirty:
        db.commit()


def rate_to_dict(r: CarrierRate, cheapest_ids: set[int]):
    return {
        "id": r.id,
        "carrier": r.carrier,
        "origin_port": r.origin_port,
        "destination_port": r.destination_port,
        "container_type": r.container_type,
        "rate_usd": r.rate_usd,
        "transit_days": r.transit_days,
        "is_cheapest": r.id in cheapest_ids,
    }


def cheapest_ids_for(results):
    best = {}
    for r in results:
        key = (r.origin_port, r.destination_port, r.container_type)
        if key not in best or r.rate_usd < best[key].rate_usd:
            best[key] = r
    return {r.id for r in best.values()}


def shipment_counts(shipments):
    counts = {"Delivered": 0, "Delayed": 0, "In Transit": 0}
    for s in shipments:
        counts[s.status] = counts.get(s.status, 0) + 1
    return counts


def parse_flex_date(raw: str) -> Optional[str]:
    if not raw:
        return None
    raw = raw.strip()
    if not raw or raw.lower() in ["nan", "none", ""]:
        return None
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def infer_carrier(text: str) -> Optional[str]:
    blob = text.lower()
    for hint, name in CARRIER_HINTS:
        if hint in blob:
            return name
    return None


def first_named_port(text: str, ports) -> Optional[str]:
    for port in sorted(ports, key=len, reverse=True):
        if re.search(rf"\b{re.escape(port)}\b", text, re.I):
            return port
    return None


def extract_rate_fields(text: str, origins=None, destinations=None):
    origins = origins or ["Nhava Sheva", "Mundra", "Chennai", "Kolkata"]
    destinations = destinations or ["Los Angeles", "New York", "Savannah", "Houston", "Long Beach", "Norfolk"]
    usd = re.search(r"USD\s*([\d,]+(?:\.\d+)?)", text, re.I)
    cont = re.search(r"(20|40)\s*(?:ft|'|teu|dry|container)?", text, re.I)
    orig = first_named_port(text, origins)
    dest = first_named_port(text, destinations)
    if usd and cont and orig and dest:
        size = cont.group(1)
        cont_str = f"{size}ft"
        amt_str = usd.group(1).replace(",", "")
        try:
            amt = float(amt_str)
        except ValueError:
            return None
        return orig, dest, cont_str, amt
    return None


def find_rate(db: Session, origin: str, dest: str, container: str, carrier: Optional[str] = None):
    origin_norm = origin.strip()
    dest_norm = dest.strip()
    container_norm = container.strip().lower()

    lane_query = db.query(CarrierRate).filter(
        func.lower(CarrierRate.origin_port) == origin_norm.lower(),
        func.lower(CarrierRate.destination_port) == dest_norm.lower(),
        func.lower(CarrierRate.container_type) == container_norm,
    )

    if carrier:
        carrier_norm = carrier.strip()
        hit = lane_query.filter(
            func.lower(CarrierRate.carrier) == carrier_norm.lower()
        ).first()
        if hit:
            return hit
        hit_loose = lane_query.filter(
            CarrierRate.carrier.ilike(f"%{carrier_norm}%")
        ).first()
        if hit_loose:
            return hit_loose
        return None

    return lane_query.order_by(CarrierRate.rate_usd.asc()).first()


def dashboard_context(request: Request, db: Session, extra=None):
    user = get_current_user(request)
    ctx = {"request": request, "user": user}
    if extra:
        ctx.update(extra)
    if not user:
        return ctx
    rates = db.query(CarrierRate).order_by(CarrierRate.origin_port, CarrierRate.destination_port, CarrierRate.rate_usd).all()
    shipments = db.query(Shipment).order_by(Shipment.shipment_id).all()
    refresh_shipment_status(shipments, db)
    cheapest = cheapest_ids_for(rates)
    ctx.update({
        "rates": rates,
        "shipments": shipments,
        "cheapest_ids": cheapest,
        "origins": sorted({r.origin_port for r in rates}),
        "destinations": sorted({r.destination_port for r in rates}),
        "carriers": sorted({r.carrier for r in rates}),
        "counts": shipment_counts(shipments),
    })
    return ctx


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("dashboard.html", dashboard_context(request, db))


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.username == username).first()
    if db_user and pwd_context.verify(password, db_user.password_hash):
        request.session["user"] = {"username": db_user.username, "role": db_user.role}
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "login_error": "Invalid credentials", "user": None},
    )


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)


@app.get("/api/rates")
def search_rates(
    request: Request,
    origin: str = "",
    destination: str = "",
    container: str = "",
    db: Session = Depends(get_db),
):
    require_user(request)
    query = db.query(CarrierRate)
    if origin:
        o = origin.strip()
        query = query.filter(
            (func.lower(CarrierRate.origin_port) == o.lower()) |
            (CarrierRate.origin_port.ilike(f"%{o}%"))
        )
    if destination:
        d = destination.strip()
        query = query.filter(
            (func.lower(CarrierRate.destination_port) == d.lower()) |
            (CarrierRate.destination_port.ilike(f"%{d}%"))
        )
    if container:
        c = container.strip()
        query = query.filter(
            (func.lower(CarrierRate.container_type) == c.lower()) |
            (CarrierRate.container_type.ilike(f"%{c}%"))
        )
    results = query.order_by(CarrierRate.rate_usd.asc()).all()
    cheapest = cheapest_ids_for(results)
    return [rate_to_dict(r, cheapest) for r in results]


def is_valid_iso_date(s: str) -> bool:
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


@app.post("/api/shipments")
def add_shipment(
    request: Request,
    shipment_id: str = Form(...),
    carrier: str = Form(...),
    origin_port: str = Form(...),
    destination_port: str = Form(...),
    container_type: str = Form(...),
    departure_date: str = Form(...),
    expected_arrival: str = Form(...),
    db: Session = Depends(get_db),
):
    require_admin(request)
    dep = departure_date.strip()
    arr = expected_arrival.strip()
    if not is_valid_iso_date(dep):
        raise HTTPException(status_code=400, detail="Invalid departure date. Use YYYY-MM-DD.")
    if not is_valid_iso_date(arr):
        raise HTTPException(status_code=400, detail="Invalid expected arrival date. Use YYYY-MM-DD.")
    if dep > arr:
        raise HTTPException(status_code=400, detail="Departure date must be before expected arrival date.")
    today_str = datetime.now().strftime("%Y-%m-%d")
    initial_status = "Delayed" if arr < today_str else "In Transit"
    db.add(Shipment(
        shipment_id=shipment_id.strip(),
        carrier=carrier.strip(),
        origin_port=origin_port.strip(),
        destination_port=destination_port.strip(),
        container_type=container_type.strip(),
        departure_date=dep,
        expected_arrival=arr,
        status=initial_status,
    ))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Shipment ID already exists")
    return {"message": "Shipment created successfully"}


def audit_one(db: Session, shipment_id: str, invoice_amount: float):
    shipment = db.query(Shipment).filter(Shipment.shipment_id == shipment_id.strip()).first()
    if not shipment:
        return 404, {"status": "Error", "message": f"Shipment ID '{shipment_id}' not found."}
    rate = find_rate(db, shipment.origin_port, shipment.destination_port, shipment.container_type, shipment.carrier)
    if not rate:
        return 400, {
            "status": "Error",
            "message": (
                f"No contracted rate found for carrier '{shipment.carrier}' on route "
                f"{shipment.origin_port} -> {shipment.destination_port} ({shipment.container_type})."
            ),
        }
    shipment.invoice_amount_usd = invoice_amount
    db.commit()
    diff = round(invoice_amount - rate.rate_usd, 2)
    if diff <= 0:
        return 200, {
            "status": "Correct",
            "contracted_rate": rate.rate_usd,
            "invoice_amount": invoice_amount,
            "difference": abs(diff),
            "message": "Invoice matches or is below the contracted rate.",
        }
    return 200, {
        "status": "Overcharged",
        "contracted_rate": rate.rate_usd,
        "invoice_amount": invoice_amount,
        "overcharge_amount": diff,
        "message": f"Overcharged by ${diff} USD",
    }


@app.post("/api/check-invoice")
def check_invoice(
    request: Request,
    shipment_id: str = Form(...),
    invoice_amount: float = Form(...),
    db: Session = Depends(get_db),
):
    require_user(request)
    code, body = audit_one(db, shipment_id, invoice_amount)
    if code != 200:
        return JSONResponse(status_code=code, content=body)
    return body


@app.post("/api/check-invoice-file")
async def check_invoice_file(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    require_user(request)
    raw = await file.read()
    try:
        df = pd.read_csv(io.BytesIO(raw))
    except Exception:
        raise HTTPException(status_code=400, detail="Could not read CSV. Need shipment_id,invoice_amount columns.")
    cols = {c.strip().lower(): c for c in df.columns}
    if "shipment_id" not in cols or ("invoice_amount" not in cols and "invoice_amount_usd" not in cols):
        raise HTTPException(status_code=400, detail="CSV must include shipment_id and invoice_amount columns.")
    amt_col = cols.get("invoice_amount") or cols.get("invoice_amount_usd")
    results = []
    for _, row in df.iterrows():
        sid = str(row[cols["shipment_id"]]).strip()
        try:
            amt = float(row[amt_col])
        except (TypeError, ValueError):
            results.append({"shipment_id": sid, "status": "Error", "message": "Invalid amount"})
            continue
        _, body = audit_one(db, sid, amt)
        body["shipment_id"] = sid
        results.append(body)
    return {"results": results}


@app.get("/api/export/{kind}")
def export_excel(kind: str, request: Request, db: Session = Depends(get_db)):
    require_user(request)
    if kind == "rates":
        rows = db.query(CarrierRate).order_by(CarrierRate.rate_usd).all()
        df = pd.DataFrame([{
            "carrier": r.carrier,
            "origin_port": r.origin_port,
            "destination_port": r.destination_port,
            "container_type": r.container_type,
            "rate_usd": r.rate_usd,
            "transit_days": r.transit_days,
        } for r in rows])
        filename = "carrier_rates_export.csv"
    elif kind == "shipments":
        rows = db.query(Shipment).order_by(Shipment.shipment_id).all()
        df = pd.DataFrame([{
            "shipment_id": s.shipment_id,
            "carrier": s.carrier,
            "origin_port": s.origin_port,
            "destination_port": s.destination_port,
            "container_type": s.container_type,
            "departure_date": s.departure_date,
            "expected_arrival": s.expected_arrival,
            "delivered_date": s.delivered_date,
            "invoice_amount_usd": s.invoice_amount_usd,
            "status": s.status,
        } for s in rows])
        filename = "shipments_export.csv"
    else:
        raise HTTPException(status_code=404, detail="Unknown export")
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/parse-emails")
def parse_emails(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    emails_file = os.path.join(BASE_DIR, "sample_emails.txt")
    if not os.path.exists(emails_file):
        return JSONResponse(status_code=404, content={"error": "sample_emails.txt not found"})

    with open(emails_file, "r", encoding="utf-8") as f:
        content = f.read()

    emails = re.split(r"-{10,}|={10,}", content)
    updated_rates = 0
    updated_shipments = 0
    skipped_emails = []
    origins = [r[0] for r in db.query(CarrierRate.origin_port).distinct().all()]
    destinations = [r[0] for r in db.query(CarrierRate.destination_port).distinct().all()]

    for idx, email_text in enumerate(emails, start=1):
        text = email_text.strip()
        if not text or text.lower().startswith("sample email inbox"):
            continue

        carrier = infer_carrier(text)
        rate_fields = extract_rate_fields(text, origins, destinations)
        if rate_fields:
            orig, dest, container, rate_val = rate_fields
            cr = find_rate(db, orig, dest, container, carrier)
            if cr:
                cr.rate_usd = rate_val
                updated_rates += 1
                continue

        shp_match = re.search(r"(SHP\d+)", text, re.I)
        status_match = re.search(r"\b(Delayed|Delivered|In Transit)\b", text, re.I)
        if shp_match and status_match:
            shp_id = shp_match.group(1).upper()
            st_val = status_match.group(1).title() if status_match.group(1).lower() != "in transit" else "In Transit"
            shp = db.query(Shipment).filter(Shipment.shipment_id == shp_id).first()
            if shp:
                shp.status = st_val
                if st_val == "Delivered":
                    deliv_date_match = re.search(
                        r"on\s+(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|\d{4}-\d{2}-\d{2})",
                        text,
                        re.I,
                    )
                    if deliv_date_match:
                        shp.delivered_date = parse_flex_date(deliv_date_match.group(1))
                updated_shipments += 1
                continue

        skipped_emails.append(f"Email Section {idx}")

    db.commit()
    return {
        "message": "Parsing execution complete.",
        "updated_rates": updated_rates,
        "updated_shipments": updated_shipments,
        "skipped_count": len(skipped_emails),
        "skipped_details": skipped_emails,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
