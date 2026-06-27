# 단일 이미지로 collector / api 등 서비스 공용 (command로 분기)
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 기본은 API. compose에서 command로 오버라이드.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
