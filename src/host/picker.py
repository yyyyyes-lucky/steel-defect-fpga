"""A native Windows picker; browser input remains available as a fallback."""
import json
import sys
import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)
if sys.argv[1] == 'folder':
    path = filedialog.askdirectory(parent=root, title='选择动态演示保存目录')
else:
    path = filedialog.askopenfilename(parent=root, title='选择六类 YOLO 模型', filetypes=[('YOLO weights', '*.pt')])
root.destroy()
sys.stdout.buffer.write(json.dumps({'path': path}, ensure_ascii=False).encode('utf-8'))
