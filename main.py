from pydantic import BaseModel
from typing import Dict, List

import json
from calendar import monthrange
from datetime import date


class Parameters(BaseModel):
    apartments_json_path: str = 'data/apartments.json'
    tenants_json_path: str = 'data/tenants.json'
    transfers_json_path: str = 'data/transfers.json'
    bills_json_path: str = 'data/bills.json'


class Room(BaseModel):
    name: str
    area_m2: float


class Apartment(BaseModel):
    key: str
    name: str
    location: str
    area_m2: float
    rooms: Dict[str, Room]

    @staticmethod
    def from_json_file(file_path: str) -> Dict[str,'Apartment']:
        data = None
        with open(file_path, 'r') as file:
            data = json.load(file)
        assert isinstance(data, dict), "Expected a dictionary of apartments"
        return {key: Apartment(**apartment) for key, apartment in data.items()}

    
class Tenant(BaseModel):
    name: str
    apartment: str
    room: str
    rent_pln: float
    deposit_pln: float
    date_agreement_from: str
    date_agreement_to: str

    @staticmethod
    def from_json_file(file_path: str) -> Dict[str,'Tenant']:
        data = None
        with open(file_path, 'r') as file:
            data = json.load(file)
        assert isinstance(data, dict), "Expected a dictionary of tenants"
        return {key: Tenant(**tenant) for key, tenant in data.items()}
    

class Transfer(BaseModel):
    amount_pln: float
    date: str
    settlement_year: int | None
    settlement_month: int | None
    tenant: str

    @staticmethod
    def from_json_file(file_path: str) -> List['Transfer']:
        data = None
        with open(file_path, 'r') as file:
            data = json.load(file)
        assert isinstance(data, list), "Expected a list of transfers"
        return [Transfer(**transfer) for transfer in data]


class Bill(BaseModel):
    amount_pln: float
    date_due: str
    apartment: str
    settlement_year: int
    settlement_month: int
    type: str

    @staticmethod
    def from_json_file(file_path: str) -> List['Bill']:
        data = None
        with open(file_path, 'r') as file:
            data = json.load(file)
        assert isinstance(data, list), "Expected a list of bills"
        return [Bill(**bill) for bill in data]


class ApartmentSettlement(BaseModel):
    apartment: str
    settlement_year: int
    settlement_month: int
    total_bills_amount_pln: float
    total_rent_amount_pln: float
    balance_pln: float

    @staticmethod
    def from_data(
        apartments: Dict[str, Apartment],
        tenants: Dict[str, Tenant],
        transfers: List[Transfer],
        bills: List[Bill],
    ) -> List['ApartmentSettlement']:
        periods = set()
        bills_by_period = {}
        transfers_by_period = {}

        for bill in bills:
            if bill.apartment not in apartments:
                raise ValueError(f"Unknown apartment in bill: {bill.apartment}")
            period = (bill.apartment, bill.settlement_year, bill.settlement_month)
            periods.add(period)
            bills_by_period[period] = bills_by_period.get(period, 0) + bill.amount_pln

        for transfer in transfers:
            if transfer.settlement_year is None or transfer.settlement_month is None:
                continue
            if transfer.tenant not in tenants:
                raise ValueError(f"Unknown tenant in transfer: {transfer.tenant}")
            tenant = tenants[transfer.tenant]
            if tenant.apartment not in apartments:
                raise ValueError(f"Unknown apartment for tenant: {tenant.apartment}")
            period = (tenant.apartment, transfer.settlement_year, transfer.settlement_month)
            periods.add(period)
            transfers_by_period[period] = (
                transfers_by_period.get(period, 0) + transfer.amount_pln
            )

        settlements = []
        for apartment, year, month in sorted(periods):
            month_start = date(year, month, 1)
            month_end = date(year, month, monthrange(year, month)[1])
            total_bills = bills_by_period.get((apartment, year, month), 0)
            total_transfers = transfers_by_period.get((apartment, year, month), 0)
            total_rent = 0
            for tenant in tenants.values():
                if tenant.apartment != apartment:
                    continue
                is_active = (
                    date.fromisoformat(tenant.date_agreement_from) <= month_end
                    and date.fromisoformat(tenant.date_agreement_to) >= month_start
                )
                if is_active:
                    total_rent += tenant.rent_pln
            settlements.append(
                ApartmentSettlement(
                    apartment=apartment,
                    settlement_year=year,
                    settlement_month=month,
                    total_bills_amount_pln=total_bills,
                    total_rent_amount_pln=total_rent,
                    balance_pln=total_rent + total_bills - total_transfers,
                )
            )
        return settlements

    @staticmethod
    def load_data(parameters: Parameters) -> List['ApartmentSettlement']:
        apartments = Apartment.from_json_file(parameters.apartments_json_path)
        tenants = Tenant.from_json_file(parameters.tenants_json_path)
        transfers = Transfer.from_json_file(parameters.transfers_json_path)
        bills = Bill.from_json_file(parameters.bills_json_path)
        return ApartmentSettlement.from_data(apartments, tenants, transfers, bills)


class TenantSettlement(BaseModel):
    tenant: str                         # klucz najemcy, np. 'tenant-1'
    settlement_year: int
    settlement_month: int
    apartment_settlement: ApartmentSettlement  # rozliczenie mieszkania, którego dotyczy
    rent_amount_pln: float              # do zapłaty: czynsz
    bills_amount_pln: float             # do zapłaty: część rachunków
    total_due_amount_pln: float         # do zapłaty razem (czynsz + rachunki)
    transfers_amount_pln: float         # suma przelewów za ten miesiąc
    balance_pln: float                  # saldo = przelewy - koszty (+ nadpłata, - niedopłata)

    @staticmethod
    def from_data(
        apartment_settlements: List[ApartmentSettlement],
        tenants: Dict[str, Tenant],
        transfers: List[Transfer],
    ) -> List['TenantSettlement']:
        settlements = []
        for apartment_settlement in apartment_settlements:
            year = apartment_settlement.settlement_year
            month = apartment_settlement.settlement_month
            month_start = date(year, month, 1)
            month_end = date(year, month, monthrange(year, month)[1])

            apartment_tenants = {
                key: tenant for key, tenant in tenants.items()
                if tenant.apartment == apartment_settlement.apartment
            }
            active_tenants = {
                key for key, tenant in apartment_tenants.items()
                if date.fromisoformat(tenant.date_agreement_from) <= month_end
                and date.fromisoformat(tenant.date_agreement_to) >= month_start
            }
            # rachunki dzielimy po równo między najemców aktywnych w danym miesiącu
            bills_share = (
                apartment_settlement.total_bills_amount_pln / len(active_tenants)
                if active_tenants else 0
            )

            for key, tenant in apartment_tenants.items():
                paid = sum(
                    transfer.amount_pln for transfer in transfers
                    if transfer.tenant == key
                    and transfer.settlement_year == year
                    and transfer.settlement_month == month
                )
                is_active = key in active_tenants
                if not is_active and paid == 0:
                    continue  # najemca nie ma nic wspólnego z tym miesiącem

                rent = tenant.rent_pln if is_active else 0
                bills = round(bills_share, 2) if is_active else 0
                total_due = rent + bills
                settlements.append(
                    TenantSettlement(
                        tenant=key,
                        settlement_year=year,
                        settlement_month=month,
                        apartment_settlement=apartment_settlement,
                        rent_amount_pln=rent,
                        bills_amount_pln=bills,
                        total_due_amount_pln=total_due,
                        transfers_amount_pln=paid,
                        balance_pln=round(paid - total_due, 2),
                    )
                )
        return settlements


class Manager:
    def __init__(self, parameters: Parameters):
        self.parameters = parameters 

        self.apartments = {}
        self.tenants = {}
        self.transfers = []
        self.bills = []
        self.settlements = []
        self.load_data()

    def load_data(self):
        self.apartments = Apartment.from_json_file(self.parameters.apartments_json_path)
        self.tenants = Tenant.from_json_file(self.parameters.tenants_json_path)
        self.transfers = Transfer.from_json_file(self.parameters.transfers_json_path)
        self.bills = Bill.from_json_file(self.parameters.bills_json_path)
        self.settlements = ApartmentSettlement.from_data(
            self.apartments, self.tenants, self.transfers, self.bills
        )
        self.tenant_settlements = TenantSettlement.from_data(
            self.settlements, self.tenants, self.transfers
        )

if __name__ == '__main__':
    parameters = Parameters()
    manager = Manager(parameters)

    print("====== APARTMENTS ======")
    for apartment in manager.apartments.values():
        print("Apartment:", apartment.key, "Name:", apartment.name, "Location:", apartment.location, "Area (m2):", apartment.area_m2)
        for room in apartment.rooms.values():
            print(f'  {room.name:<15}: {room.area_m2:<4} m2')

        print(" *Bills:")
        for bill in manager.bills:
            if bill.apartment == apartment.key:
                print(f'    Bill: {bill.amount_pln:<10} {bill.date_due} {bill.settlement_year} {bill.settlement_month} {bill.type}')
        print()

    print("====== TENANTS ======")
    for tenant in manager.tenants.values():
        print(f"Tenant: {tenant.name}")
        print(f"  Apartment      : {tenant.apartment}")
        print(f"  Room           : {tenant.room}")
        print(f"  Rent           : {tenant.rent_pln}")
        print(f"  Deposit        : {tenant.deposit_pln}")
        print(f"  Agreement From : {tenant.date_agreement_from}")
        print(f"  Agreement      : {tenant.date_agreement_to}")
        print(f" *Transfers:")
        for transfer in manager.transfers:
            if transfer.tenant == tenant.name:
                print(f'    Transfer: {transfer.amount_pln:<10} {transfer.date} {transfer.settlement_year} {transfer.settlement_month}')
        print()