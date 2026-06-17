# Bank Management System

A desktop-based banking application built with **Python**, **Tkinter**, and **SQLite3**.  
This project simulates core banking operations such as account creation, deposits, withdrawals, loan management, balance statements, admin access, and Mudarabah profit distribution.

---

## Features

- **Open New Account**
  - Create customer accounts with unique account numbers
  - Validate mobile number and NID/passport
  - Store initial deposit
  - Support both **Conventional** and **Shariah-Based Mudarabah** accounts

- **Deposit Money**
  - Deposit funds into any valid account
  - Automatically apply accrued interest before transaction
  - Record every transaction in the database

- **Withdraw Money**
  - Withdraw money from an account
  - Enforce minimum reserve balance
  - Keep transaction history updated

- **Loan Management**
  - Provide conventional loan disbursement
  - Calculate maximum eligible loan based on balance
  - Allow loan repayment
  - Restrict interest-based loans for Shariah accounts

- **Account Dashboard**
  - View full account details
  - See balance, loan status, and last update time
  - Show recent transaction history

- **Mudarabah Profit Distribution**
  - Calculate bank profit from transaction data
  - Distribute profit to active Shariah accounts proportionally
  - Show bank share, statutory reserve, and retained earnings
  - Preview profit distribution before committing changes

- **Admin Portal**
  - View all registered accounts
  - Inspect balances, loans, and timestamps
  - Refresh live account data
  - Sort table columns easily

- **Database Migration Support**
  - Handles older schema versions safely
  - Migrates legacy `username` fields to `accountno` when needed

---

## Tech Stack

- **Python 3**
- **Tkinter** for GUI
- **SQLite3** for local database storage
- **hashlib** for password hashing
- **datetime** for timestamps
- **re** for input validation
- **random** for account number generation

---

## Required Libraries

This project uses only Python standard library modules, so no external installation is needed.

You do **not** need to install anything with `pip`.

### Imported modules used in the project
```python
import tkinter as tk
from tkinter import messagebox, ttk
import sqlite3
import random
import hashlib
import re
from datetime import datetime
```

### Run command
```bash
python Bank_managment_system.py
```

---

## Core Concepts Used

This project demonstrates:

- GUI application development
- Database design and CRUD operations
- Transaction logging
- Validation and error handling
- Interest calculation
- Profit-sharing logic
- Basic banking workflow simulation
- Secure admin login using hashed password

---

## How It Works

### 1. Account Creation
A user can create a new account by entering:
- Full name
- Mobile number
- NID or passport number
- Initial deposit
- Account type

Each account gets a unique 10-digit account number.

### 2. Transactions
The system stores all deposits, withdrawals, loan actions, and profit distributions in a transaction table.

### 3. Interest and Loan Logic
- Conventional accounts can accrue savings interest.
- Conventional loans accrue loan interest.
- Shariah accounts do not receive interest-based loans.
- Mudarabah profit is distributed from net bank profit.

### 4. Admin View
The admin panel displays all accounts in a structured table with sorting support.

---

## Database Structure

The application creates and manages two main tables:

### `accounts`
Stores:
- account number
- name
- mobile
- NID
- account type
- balance
- loan
- last update timestamp

### `transactions`
Stores:
- transaction ID
- account number
- transaction type
- amount
- timestamp

---

## Default Admin Login

> **Warning:** Change the default admin password before deployment.

- **Username:** Admin panel uses password authentication
- **Default Password:** `admin`

The password is stored as a SHA-256 hash in the code.

---

## Validation Rules

The app includes validation for:

- **Mobile number**
  - Must match Bangladeshi format: `01XXXXXXXXX`

- **NID/passport**
  - Accepts 10 or 17 digits, or a valid passport format

- **Initial deposit**
  - Must be at least the minimum required amount

- **Withdrawal**
  - Cannot reduce balance below the reserve limit

- **Loan repayment**
  - Cannot exceed outstanding loan
  - Cannot reduce balance below reserve

---

## Project Highlights

- Fully functional desktop GUI
- Persistent local database
- Transaction history tracking
- Conventional and Shariah account support
- Mudarabah-style profit sharing
- Admin dashboard with sortable data
- Clean separation of banking logic and UI

---

## Screenshots

Add screenshots here after running the app:

- Main Menu
- Account Creation
- Deposit Window
- Withdraw Window
- Account Dashboard
- Loan Management
- Mudarabah Panel
- Admin Portal

Example:

```md



```

---

## Installation

### Requirements
- Python 3.10+ recommended

### Steps

1. Clone the repository:
```bash
git clone https://github.com/your-username/bank-management-system.git
cd bank-management-system
```

2. Run the application:
```bash
python Bank_managment_system.py
```

No external packages are required beyond Python’s standard library.

---

## File Description

- `Bank_managment_system.py` — Main application file containing GUI, database, and banking logic.
- `banksystem.db` — SQLite database file created automatically when the app runs.

---

## Future Improvements

Possible upgrades for this project:

- Add user login system for customers
- Add password reset functionality
- Build a web version using Flask or Django
- Add charts and financial reports
- Add export to CSV/PDF
- Add search and filtering in admin portal
- Add multi-user role-based authentication
- Add ML-based loan risk scoring
- Improve UI design with a modern theme

---

## Learning Outcome

This project helped develop skills in:

- Python GUI programming
- SQLite database management
- Banking workflow simulation
- Financial transaction logic
- Validation and exception handling
- Secure password hashing
- Object-oriented design and application structure

---

## Disclaimer

This project is **for educational purposes only**.  
It simulates banking operations and is **not intended for real-world financial deployment** without major security, compliance, and audit improvements.

---

## Author

**Jabid Muntasir**  
Bangladesh  
Interested in Machine Learning, Competitive Programming, and Software Development

---

## License

You may add a license of your choice, such as MIT.

---

## Star This Project

If you like this project, consider starring the repository to support future improvements.
