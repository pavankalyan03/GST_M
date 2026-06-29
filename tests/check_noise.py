import fitz
import sys

doc = fitz.open(r"c:\Users\SASI KOTHA\Desktop\GST_M\processed_sample2.pdf")
page = doc[0]
drawings = page.get_drawings()
noise_drawings = [d for d in drawings if (d.get('fill_opacity') or 1) < 0.1]
print(f"Total drawings: {len(drawings)}")
print(f"Noise drawings (opacity < 0.1): {len(noise_drawings)}")
if noise_drawings:
    print("Example noise:", noise_drawings[0])
doc.close()
