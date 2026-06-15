# OPNGMS backend image — used for three services (api / worker / migrate), differing only by command.
# Pinned to 3.14 to match the development/test runtime (the code relies on PEP 649 deferred annotation
# evaluation, the default in 3.14; on 3.12 an eagerly-evaluated annotation like `-> list[EventTopRow]`
# clashes with the `list` method on EventRepository).
FROM python:3.14-slim

# Apply Debian security updates to the (sometimes-stale) base image, then install the WeasyPrint
# runtime dependencies (pango/cairo/gdk-pixbuf) for PDF report rendering (Phase 5). The `upgrade`
# pulls base-image security patches at build time (e.g. the openssl deb13u2 fix for CVE-2026-45447)
# so the image stays patched on every rebuild without chasing individual CVEs.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
        shared-mime-info \
        fonts-dejavu-core \
        fonts-noto-core \
        fonts-noto-cjk \
        tzdata \
        openssl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Upgrade pip first to close the pip-audit finding (pip 26.1.1 → ≥26.1.2).
RUN pip install --no-cache-dir --upgrade pip

# Install the package (non-editable). Dependencies (uvicorn, arq, alembic, weasyprint, …) come from pyproject.
COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-cache-dir .

# Run as a non-root user (defense-in-depth).
RUN useradd --create-home --uid 10001 opngms && chown -R opngms:opngms /app
USER opngms

EXPOSE 8000

# Default command = the API. The worker and migrate services override this in compose.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
