# Long-Term Value Screener — self-hostable image.
# One container serves both the API and the web UI. State (watchlist, weekly
# track record, dated reports) persists in /app/reports — mount a volume for it.
FROM python:3.14-slim

# ca-certificates for HTTPS to SEC/Yahoo; tzdata so the scheduler honours TZ.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Deps first for layer caching. curl_cffi ships self-contained manylinux wheels
# (bundled curl-impersonate), so no compiler/libcurl is needed.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persistent state — override SEC_EDGAR_UA with your own contact (SEC fair-access).
ENV SEC_EDGAR_UA="stock-valuation self-hosted — set SEC_EDGAR_UA to your email"
VOLUME ["/app/reports"]
EXPOSE 8000

# Bind 0.0.0.0 (not 127.0.0.1) so the port is reachable outside the container.
CMD ["uvicorn", "main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
