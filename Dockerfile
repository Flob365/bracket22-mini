FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY bracket22 ./bracket22
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "bracket22.app:app", "--host", "0.0.0.0", "--port", "8000"]
