FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY static ./static
ENV DB_PATH=/data/avisador.db
VOLUME /data
EXPOSE 8000
# Un solo proceso: los avisos en tiempo real se reparten desde memoria
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
