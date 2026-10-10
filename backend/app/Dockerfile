FROM python:3.13-slim

# 与本地 .venv（Python 3.13.15）保持同一大版本，避免依赖解析差异
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    TZ=Asia/Shanghai \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 依赖单独占一层：只要 requirements.docker.txt 不变，
# 后续修改业务代码时 build 会直接命中缓存，不会重装依赖。
COPY requirements.docker.txt /app/requirements.docker.txt
RUN pip install --no-cache-dir -r /app/requirements.docker.txt

# 业务代码后置，改代码只让这一层失效
COPY . /app/

EXPOSE 8200

# 生产模式：不使用 --reload
CMD ["uvicorn", "app_main:app", "--host", "0.0.0.0", "--port", "8200"]
