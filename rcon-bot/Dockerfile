FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir mcrcon
RUN pip install --no-cache-dir docker 
COPY main.py .

CMD ["python", "-u", "main.py"]