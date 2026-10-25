# 雲端部署用（Render、Hugging Face 等）；本機執行不需要
FROM python:3.12-slim

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PORT=7860
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY --chown=user . .

# 單一 worker，讓股價／財報快取共用；埠號由主機的 PORT 環境變數決定
CMD gunicorn -b 0.0.0.0:${PORT:-7860} -w 1 --threads 8 --timeout 180 app:app
