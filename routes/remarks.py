# routes/remarks.py
from fastapi import APIRouter, UploadFile, File, Response, Depends, HTTPException
from fastapi.responses import StreamingResponse
from routes.auth import get_current_user
from database.crud import get_remarks, add_remark, delete_remark, clear_remarks
from pydantic import BaseModel
import io

router = APIRouter(prefix="/api/remarks", tags=["remarks"])

class RemarkItem(BaseModel):
    username: str
    remark_name: str
    is_highlight: int = 0

@router.get("", dependencies=[Depends(get_current_user)])
async def list_remarks():
    """
    获取监控备注列表与总数量
    """
    remarks = get_remarks()
    return {
        "count": len(remarks),
        "remarks": remarks
    }

@router.post("", dependencies=[Depends(get_current_user)])
async def save_remark(data: RemarkItem):
    """
    新增或修改单个备注信息
    """
    username = data.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    add_remark(username, data.remark_name.strip(), data.is_highlight)
    return {"message": "备注保存成功"}

@router.delete("/{username}", dependencies=[Depends(get_current_user)])
async def remove_remark(username: str):
    """
    删除单个备注信息
    """
    delete_remark(username)
    return {"message": "备注删除成功"}

@router.post("/import", dependencies=[Depends(get_current_user)])
async def import_remarks(file: UploadFile = File(...)):
    """
    从 TXT 文件中批量导入备注名。
    文件每行格式: @用户名\t备注名[\t高亮0或1]
    """
    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = content.decode("gbk")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="文本编码解析失败，请使用 UTF-8 或 GBK 格式的 TXT 文件")

    lines = text.splitlines()
    success_count = 0
    
    # 导入前先清空旧数据以防止冲突？或者增量导入？
    # 用户一般希望是增量覆盖导入，所以直接用 SQLite 的 INSERT OR REPLACE
    for line_num, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        
        # 兼容 Tab 分隔或空格分隔
        parts = line.split("\t")
        if len(parts) < 2:
            parts = line.split(maxsplit=2)
            
        if len(parts) < 2:
            continue # 格式不合规的行直接跳过
            
        username = parts[0].strip()
        remark_name = parts[1].strip()
        
        is_highlight = 0
        if len(parts) >= 3:
            try:
                is_highlight = int(parts[2].strip())
            except ValueError:
                is_highlight = 0
                
        if username and remark_name:
            add_remark(username, remark_name, is_highlight)
            success_count += 1
            
    return {"message": f"成功导入 {success_count} 个备注配置"}

@router.get("/export", dependencies=[Depends(get_current_user)])
async def export_remarks():
    """
    将所有备注配置导出为 TXT 文件
    """
    remarks = get_remarks()
    
    output = io.StringIO()
    output.write("# 监控备注配置文件\n")
    output.write("# 格式：@用户名\\t备注名\\t高亮标记(1启用/0禁用)\n")
    for r in remarks:
        output.write(f"@{r['username']}\t{r['remark_name']}\t{r['is_highlight']}\n")
        
    txt_content = output.getvalue()
    output.close()
    
    headers = {
        'Content-Disposition': 'attachment; filename="remarks_export.txt"'
    }
    return Response(content=txt_content, media_type="text/plain", headers=headers)

@router.get("/template")
async def download_template():
    """
    获取导入备注名模板文件
    """
    template_content = (
        "# 监控备注名批量导入模板\n"
        "# 格式：每行一条数据，以 Tab(制表符) 分开\n"
        "# @推特用户名\t备注别名\t高亮回复(1开启,0关闭)\n"
        "@traininghone\t子网5\t1\n"
        "@elonmusk\t马斯克\t0\n"
        "@vitalikbuterin\tV神\t1\n"
    )
    
    headers = {
        'Content-Disposition': 'attachment; filename="remarks_template.txt"'
    }
    return Response(content=template_content, media_type="text/plain", headers=headers)
