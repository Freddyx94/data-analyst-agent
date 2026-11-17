# The chat app in a container.
#
# The data and the API key are NOT in the image: the M5 data may not be
# redistributed, and a key must never be baked into an image. Both are given
# when the container starts:
#
#   docker build -t data-analyst-agent .
#   docker run -p 8501:8501 --env-file .env -v "$PWD/data:/app/data:ro" data-analyst-agent
#
# Then open http://localhost:8501

FROM python:3.11-slim

WORKDIR /app

# Dependencies first: this layer is reused as long as requirements.txt is unchanged.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The project itself. Installed in editable mode on purpose: the code finds
# config.yaml, docs/ and prompts/ relative to the project folder.
COPY . .
RUN pip install --no-cache-dir -e .

EXPOSE 8501
CMD ["streamlit", "run", "app/streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
