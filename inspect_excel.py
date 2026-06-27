import openpyxl

wb = openpyxl.load_workbook('invoices.xlsx', data_only=True)
sheet = wb['B2B']

with open('excel_inspection.txt', 'w', encoding='utf-8') as f:
    for i, row in enumerate(sheet.iter_rows(min_row=1, max_row=10, values_only=True)):
        f.write(f"Row {i+1}: {row}\n")
