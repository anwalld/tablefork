FROM python:3.12-slim
RUN pip install --no-cache-dir pytest==8.3.5 requests==2.32.3 websockets==13.1 pymysql==1.1.1 cryptography==44.0.2 redis==5.2.1
WORKDIR /tests
COPY . .
CMD ["pytest", "-q"]
