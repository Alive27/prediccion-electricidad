FROM python:3.10-slim
WORKDIR /app
# Aquí instalarás FastAPI y Uvicorn
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Exponemos el puerto de FastAPI
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]