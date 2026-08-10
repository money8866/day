with open(r'D:\mystock\report_daily\_market_report_pdf.py', encoding='utf-8') as f:
    content = f.read()

# 找到错误的 kv_table 调用并替换为通用的 multi_col_table
old = "                kcw = [5*cm, 13.4*cm]\n                story.append(kv_table(kv, kcw, color=color))"
new = "                # 4列键值表（分两列显示）\n                half = (len(kv)+1)//2\n                row1 = kv[:half]; row2 = kv[half:]\n                while len(row2) < len(row1): row2.append((' ', ' '))\n                data = [['指标','数值','指标','数值']]\n                for (k1,v1),(k2,v2) in zip(row1, row2):\n                    data.append([k1, v1, k2, v2])\n                cw = [3.2*cm]*4\n                t = Table(data, colWidths=cw)\n                t.setStyle(TableStyle([\n                    ('BACKGROUND',(0,0),(-1,0),color),\n                    ('TEXTCOLOR',(0,0),(-1,0),colors.whitesmoke),\n                    ('FONTNAME',(0,0),(-1,-1),FONT), ('FONTSIZE',(0,0),(-1,-1),8.5),\n                    ('ALIGN',(0,0),(-1,-1),'LEFT'), ('VALIGN',(0,0),(-1,-1),'MIDDLE'),\n                    ('TOPPADDING',(0,0),(-1,-1),3), ('BOTTOMPADDING',(0,0),(-1,-1),3),\n                    ('LEFTPADDING',(0,0),(-1,-1),5),\n                    ('GRID',(0,0),(-1,-1),0.5,colors.HexColor('#cccccc')),\n                    ('ROWBACKGROUNDS',(0,1),(-1,-1),\n                     [colors.HexColor('#f8f9fa'),colors.HexColor('#eef1f5')]),\n                ]))\n                story.append(t)"

assert old in content, 'target not found'
content = content.replace(old, new)
open(r'D:\mystock\report_daily\_market_report_pdf.py', 'w', encoding='utf-8').write(content)
print('patched OK')
