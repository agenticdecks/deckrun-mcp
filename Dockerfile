FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY deckrun_mcp.py .
COPY deckrun_mcp_http.py .

# HTTP transport — listens on 8082
EXPOSE 8082

# DECKRUN_API_KEY env var controls tier:
#   not set  → free tier (2 tools, free.agenticdecks.com)
#   set      → paid tier (6 tools, api.agenticdecks.com)
ENV DECKRUN_API_KEY=""

CMD ["uvicorn", "deckrun_mcp_http:starlette_app", "--host", "0.0.0.0", "--port", "8082"]
