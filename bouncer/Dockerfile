FROM python:3.12-alpine
RUN pip install --no-cache-dir librouteros
COPY bouncer.py /app/bouncer.py
WORKDIR /app
CMD ["python3", "/app/bouncer.py"]
