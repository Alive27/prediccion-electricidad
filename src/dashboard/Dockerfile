FROM python:3.10-slim
WORKDIR /app
# Aquí instalarás Streamlit y Plotly
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Exponemos el puerto de Streamlit
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]