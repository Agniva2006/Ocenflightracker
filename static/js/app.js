function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
}

async function executeRateSearch() {
    const orig = document.getElementById("src_orig").value;
    const dest = document.getElementById("src_dest").value;
    const cont = document.getElementById("src_cont").value;
    const res = await fetch(`/api/rates?origin=${encodeURIComponent(orig)}&destination=${encodeURIComponent(dest)}&container=${encodeURIComponent(cont)}`);
    const data = await res.json();
    const tbody = document.getElementById("rate_table_body");
    tbody.innerHTML = data.map((r) => `
        <tr class="border-b hover:bg-slate-50 ${r.is_cheapest ? "bg-emerald-50" : ""}">
            <td class="p-3 font-semibold">${escapeHtml(r.carrier)}</td>
            <td class="p-3">${escapeHtml(r.origin_port)} → ${escapeHtml(r.destination_port)}</td>
            <td class="p-3">${escapeHtml(r.container_type)}</td>
            <td class="p-3 font-bold">$${escapeHtml(r.rate_usd)}</td>
            <td class="p-3">${escapeHtml(r.transit_days)} days</td>
            <td class="p-3">${r.is_cheapest
                ? '<span class="bg-emerald-600 text-white text-xs px-2 py-1 rounded font-bold">BEST PRICE</span>'
                : '<span class="bg-slate-100 text-slate-600 text-xs px-2 py-1 rounded">Standard</span>'}</td>
        </tr>`).join("");
}

function paintInvoiceBox(html, tone) {
    const box = document.getElementById("inv_result");
    box.classList.remove("hidden", "bg-emerald-100", "text-emerald-800", "bg-rose-100", "text-rose-800", "bg-slate-100", "text-slate-800");
    box.classList.add(...tone);
    box.innerHTML = html;
}

async function auditInvoice() {
    const shpId = document.getElementById("inv_id").value;
    const amt = document.getElementById("inv_amt").value;
    if (!shpId || !amt) {
        alert("Select a shipment and enter the billed amount.");
        return;
    }
    const formData = new FormData();
    formData.append("shipment_id", shpId);
    formData.append("invoice_amount", amt);
    const res = await fetch("/api/check-invoice", { method: "POST", body: formData });
    const data = await res.json();
    if (data.status === "Correct") {
        paintInvoiceBox(`${escapeHtml(data.message)} (Contracted $${data.contracted_rate} · Billed $${data.invoice_amount})`, ["bg-emerald-100", "text-emerald-800"]);
    } else if (data.status === "Overcharged") {
        paintInvoiceBox(`OVERCHARGE: ${escapeHtml(data.message)} (Contracted $${data.contracted_rate} · Billed $${data.invoice_amount})`, ["bg-rose-100", "text-rose-800"]);
    } else {
        paintInvoiceBox(escapeHtml(data.message || data.detail), ["bg-slate-100", "text-slate-800"]);
    }
}

async function auditInvoiceFile() {
    const file = document.getElementById("inv_file").files[0];
    if (!file) {
        alert("Choose a CSV with shipment_id,invoice_amount columns.");
        return;
    }
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch("/api/check-invoice-file", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) {
        paintInvoiceBox(escapeHtml(data.detail || "Upload failed"), ["bg-slate-100", "text-slate-800"]);
        return;
    }
    const lines = (data.results || []).map((r) => 
        `${escapeHtml(r.shipment_id)}: ${escapeHtml(r.status)}${r.message ? " — " + escapeHtml(r.message) : ""}`
    ).join("<br>");
    paintInvoiceBox(lines, ["bg-slate-100", "text-slate-800"]);
}

async function triggerEmailParsing() {
    const res = await fetch("/api/parse-emails", { method: "POST" });
    const data = await res.json();
    if (res.ok) {
        alert(`Email parsing\nRates updated: ${data.updated_rates}\nStatuses updated: ${data.updated_shipments}\nSkipped: ${data.skipped_count}`);
        location.reload();
    } else {
        alert(`Error: ${data.detail || data.error}`);
    }
}

function toggleShipmentModal() {
    document.getElementById("shipmentModal").classList.toggle("hidden");
}

async function submitNewShipment(e) {
    e.preventDefault();
    const formData = new FormData(document.getElementById("addShipmentForm"));
    const res = await fetch("/api/shipments", { method: "POST", body: formData });
    if (res.ok) {
        location.reload();
        return;
    }
    const err = await res.json();
    alert(`Failed: ${err.detail}`);
}

function filterShipmentsTable() {
    const input = document.getElementById("tracker_filter").value.toLowerCase();
    document.querySelectorAll("#shipment_table tbody tr").forEach((row) => {
        row.style.display = row.innerText.toLowerCase().includes(input) ? "" : "none";
    });
}
