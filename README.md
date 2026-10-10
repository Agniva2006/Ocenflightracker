# Ocean Freight Rate & Shipment Tracker

Web app for comparing ocean freight rates, tracking shipments, catching invoice overcharges, and applying rate/status updates from a sample email inbox.

## Run

```bash
pip install -r requirements.txt
python main.py
```

Open http://127.0.0.1:8000

| Role  | Username | Password |
|-------|----------|----------|
| Admin | `admin`  | `admin123` |
| User  | `user`   | `user123` |

## Modules

1. **Rate search** — pick origin, destination, container (20ft/40ft). Matching carriers sorted by USD; cheapest lane highlighted.
2. **Shipment tracker** — list with In Transit / Delivered / Delayed (overdue and not delivered). Admin can add shipments. Filter + status chart.
3. **Invoice checker** — compare billed USD to contracted rate, or upload a CSV (`shipment_id,invoice_amount`).
4. **Email updates** — Admin → Process inbox. Reads `sample_emails.txt`. Rate emails update the catalog; status emails update the tracker; irrelevant mail is skipped.

Export rates or shipments as CSV (opens in Excel).

## Data

- `carrier_rates.csv`
- `sample_shipments.csv`
- `sample_emails.txt`

SQLite file `freight.db` is created on first run.
