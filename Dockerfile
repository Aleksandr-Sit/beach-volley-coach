FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Данные (SQLite + фото) монтируются томом, см. docker-compose.yml
RUN mkdir -p /app/data

CMD ["python", "-m", "bot.main"]
