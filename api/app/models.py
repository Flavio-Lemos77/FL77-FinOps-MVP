from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base

class AuditMixin:
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Income(AuditMixin, Base):
    __tablename__ = "incomes"
    description: Mapped[str] = mapped_column(String(160))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    received_on: Mapped[date] = mapped_column(Date)
    category: Mapped[str] = mapped_column(String(80), default="Outros")
    recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("income_sources.id"), nullable=True)
    income_type: Mapped[str] = mapped_column(String(40), default="Salário CLT")

class IncomeSource(AuditMixin, Base):
    __tablename__ = "income_sources"
    name: Mapped[str] = mapped_column(String(160))
    monthly_salary: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    source_type: Mapped[str] = mapped_column(String(40), default="Emprego")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Category(AuditMixin, Base):
    __tablename__ = "categories"
    name: Mapped[str] = mapped_column(String(80), unique=True)
    kind: Mapped[str] = mapped_column(String(20), default="despesa")

class Expense(AuditMixin, Base):
    __tablename__ = "expenses"
    description: Mapped[str] = mapped_column(String(160))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    due_on: Mapped[date] = mapped_column(Date)
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    category: Mapped[str] = mapped_column(String(80), default="Outros")
    status: Mapped[str] = mapped_column(String(20), default="pendente")
    recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    installments_total: Mapped[int] = mapped_column(default=1)
    installment_number: Mapped[int] = mapped_column(default=1)
    review_before_days: Mapped[int] = mapped_column(default=5)

class Debt(AuditMixin, Base):
    __tablename__ = "debts"
    creditor: Mapped[str] = mapped_column(String(160))
    original_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    current_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    interest_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    due_day: Mapped[int | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ativa")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

class Agreement(AuditMixin, Base):
    __tablename__ = "agreements"
    debt_id: Mapped[int] = mapped_column(ForeignKey("debts.id"))
    description: Mapped[str] = mapped_column(String(160))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    installment_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    installments_total: Mapped[int]
    installments_paid: Mapped[int] = mapped_column(default=0)
    start_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="ativo")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

class Payment(AuditMixin, Base):
    __tablename__ = "payments"
    debt_id: Mapped[int | None] = mapped_column(ForeignKey("debts.id"), nullable=True)
    agreement_id: Mapped[int | None] = mapped_column(ForeignKey("agreements.id"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    paid_on: Mapped[date] = mapped_column(Date)
    method: Mapped[str] = mapped_column(String(40), default="Pix")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

class User(AuditMixin, Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
