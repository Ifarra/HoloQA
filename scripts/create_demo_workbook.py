from pathlib import Path

from openpyxl import Workbook


def main() -> None:
    output = Path(__file__).parents[1] / "fixtures" / "demo_cases.xlsx"
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "SIT Cases"
    sheet.append(["Test ID", "Title", "Steps", "Expected Result"])
    sheet.append(["TC-001", "Create valid user", "Open /demo; Create user valid@example.com", "User created successfully"])
    sheet.append(["TC-002", "Reject invalid email", "Open /demo; Create user invalid-email", "Invalid email"])
    sheet.append(["TC-003", "Prevent duplicate user", "Open /demo; Create user duplicate@example.com; Create user duplicate@example.com", "User already exists"])
    sheet.append(["TC-004", "Simulated server failure", "Open /demo; Create user failure@example.com", "Simulated server failure"])
    workbook.save(output)
    print(output)


if __name__ == "__main__":
    main()
