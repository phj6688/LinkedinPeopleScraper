# Use an ARM-compatible Python image for Raspberry Pi
FROM python:3.11-slim-bullseye

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y gcc build-essential libglib2.0-0 libsm6 libxext6 libxrender-dev git && \
    rm -rf /var/lib/apt/lists/*

# Install Playwright and its dependencies
RUN pip install --upgrade pip
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN pip install playwright gunicorn
RUN playwright install 
RUN playwright install-deps

# Download spaCy model
RUN python -m spacy download en_core_web_sm

# Copy project files
COPY . .

EXPOSE 5000
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "app:application"]