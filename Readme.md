# 📱 Phone Shop POS Backend Server (Django)

## Overview

This project is a backend server built with Django to manage and process business transactions for a phone shop's point-of-sale (POS) system. The server performs all essential business operations, such as product management, sales transactions, and customer data handling. It exposes a REST API using Django Rest Framework (DRF) to serve data to the frontend and is deployed on an AWS EC2 instance.

## Features

- **Business Operations**: Manages key functions like product management, sales transactions, and customer data for the phone shop.
- **RESTful API**: Exposes a set of RESTful endpoints to communicate with the frontend, powered entirely by Django Rest Framework (DRF).
- **PostgreSQL Database**: Utilizes a PostgreSQL database hosted on AWS RDS for efficient data storage and retrieval.
- **Pagination**: Implements pagination for major requests to ensure that large datasets (e.g., products, transactions) are fetched efficiently.
- **Redis Caching**: Speeds up frequent data requests using Redis caching for enhanced performance.
- **MeiliSearch Integration**: Keeps search results synchronized and up-to-date on the frontend with MeiliSearch, allowing for fast and efficient searching.
- **JWT Authentication**: Firebase ID tokens are decoded to validate users and grant access tokens. This flow checks if the user exists in the database and issues a JWT for authenticated access. A separate authentication mechanism exists for a Celery application that schedules tasks related to the backend server, including sending weekly email reports about sales transactions.
- **Celery Application**: A dedicated application that handles scheduling tasks to communicate with the Django server, ensuring that asynchronous tasks are processed efficiently.
- **Dashboard Integration**: The dashboard uses data from Firebase Realtime Database (for a separate app) to display transaction data and business reports.
- **Google SMTP Integration**: The server utilizes Google SMTP to send emails, including reports and notifications.

## Security Measures

- **AWS EC2 Deployment**: The server is deployed on AWS EC2 with a robust security setup.
- **AWS RDS**: PostgreSQL database hosted securely on AWS RDS.
- **Nginx Reverse Proxy**: Nginx is used as a reverse proxy for the Django server.
- **SSL Encryption**: All traffic is secured via SSL, with a custom domain configured using AWS Route53.
- **WhiteNoise Integration**: The server uses WhiteNoise to serve static files with proper cache control headers and ensures better performance through compression and cache management.
- **CompressedManifestStaticFilesStorage**: Static files are stored using `CompressedManifestStaticFilesStorage` to enable efficient cache management.
- **HSTS & CSP**: The server enforces HTTP Strict Transport Security (HSTS) and Content Security Policy (CSP) headers to prevent common attacks such as cross-site scripting (XSS) and downgrade attacks.

### Security Tools

- **Fail2ban**: Protects the server from IP-based attacks by banning IPs after repeated failed login attempts.
- **ModSecurity WAF**: Deployed with OWASP CRS (Core Rule Set) for comprehensive web application protection.
- **Amazon Inspector**: Scans for and mitigates vulnerabilities in real-time.
- **CSP and HSTS**: Content Security Policy (CSP) and HTTP Strict Transport Security (HSTS) headers are enforced to prevent common attacks like XSS and downgrade attacks.

## Database Models and Known Issues

- **Models**: The server manages various models such as products, customers, and sales transactions in PostgreSQL.
- **Known Issue**: At one point during production, an error led to all models being prefixed with `_FIX` as a temporary workaround for creating new models.

