# 运动员体能与功能评估系统 —— 生产镜像
# 用法：
#   docker build -t efa:1.0.0 .
#   docker run -d -p 8501:8501 \
#     -e EFA_ACCESS_PASSWORD=<你的口令> \
#     -v $(pwd)/data:/app/data \
#     -v $(pwd)/logs:/app/logs \
#     -v $(pwd)/backups:/app/backups \
#     --name efa efa:1.0.0
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health').read().strip()==b'ok' else 1)"

CMD ["streamlit", "run", "app.py"]
