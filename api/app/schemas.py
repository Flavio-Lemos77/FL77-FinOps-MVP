from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)

class IncomeIn(BaseModel):
    description: str = Field(max_length=160); amount: Decimal = Field(gt=0); received_on: date
    category: str = "Outros"; recurring: bool = False; notes: str | None = None
    source_id: int | None = None
    income_type: str = "Salário CLT"
class IncomeOut(IncomeIn, ORM): id: int; created_at: datetime

class IncomeSourceIn(BaseModel):
    name: str = Field(max_length=160)
    monthly_salary: Decimal = Field(gt=0)
    source_type: str = "Emprego"
    active: bool = True
class IncomeSourceOut(IncomeSourceIn, ORM): id: int; created_at: datetime

class CategoryIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    kind: str = "despesa"
class CategoryOut(CategoryIn, ORM): id: int; created_at: datetime

class AuthIn(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=200)

class SignupIn(BaseModel):
    username: str = Field(min_length=3, max_length=80)   # usado como login (pode ser e-mail)
    password: str = Field(min_length=8, max_length=200)
    email: str = Field(min_length=5, max_length=160)
    phone: str = Field(min_length=8, max_length=40)
    recovery_email: str = Field(min_length=5, max_length=160)

class ExpenseIn(BaseModel):
    description: str = Field(max_length=160); amount: Decimal = Field(gt=0); due_on: date
    paid_on: date | None = None; category: str = "Outros"; status: str = "pendente"; recurring: bool = False; notes: str | None = None
    installments_total: int = Field(default=1, ge=1, le=600)
    installment_number: int = Field(default=1, ge=1)
    review_before_days: int = Field(default=5, ge=0, le=30)
class ExpenseOut(ExpenseIn, ORM): id: int; created_at: datetime

class DebtIn(BaseModel):
    creditor: str = Field(max_length=160)
    debt_type: str = "Outros"
    original_amount: Decimal = Field(gt=0)
    current_balance: Decimal = Field(ge=0)
    monthly_installment: Decimal | None = Field(default=None, ge=0)
    interest_rate: Decimal | None = Field(default=None, ge=0)
    due_day: int | None = Field(default=None, ge=1, le=31)
    status: str = "ativa"
    notes: str | None = None
class DebtOut(DebtIn, ORM): id: int; created_at: datetime

class AgreementIn(BaseModel):
    debt_id: int; description: str = Field(max_length=160); total_amount: Decimal = Field(gt=0); installment_amount: Decimal = Field(gt=0)
    installments_total: int = Field(gt=0); installments_paid: int = Field(default=0, ge=0); start_on: date; status: str = "ativo"; notes: str | None = None
class AgreementOut(AgreementIn, ORM): id: int; created_at: datetime

class PaymentIn(BaseModel):
    debt_id: int | None = None; agreement_id: int | None = None; amount: Decimal = Field(gt=0); paid_on: date; method: str = "Pix"; notes: str | None = None
    @model_validator(mode="after")
    def link_required(self):
        if not self.debt_id and not self.agreement_id: raise ValueError("Informe uma dívida ou um acordo")
        return self
class PaymentOut(PaymentIn, ORM): id: int; created_at: datetime
