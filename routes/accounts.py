# routes/accounts.py
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from routes.auth import get_current_user
from database.crud import get_accounts, add_account, delete_account
from services.twitter_crawler import test_account_and_proxy

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

class AccountCreate(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    email: str = Field(..., min_length=1)
    proxy: str = Field("", description="格式: http://user:pass@ip:port")
    remark: str = Field("")

@router.get("", dependencies=[Depends(get_current_user)])
async def list_accounts():
    """
    获取账号池列表
    """
    return get_accounts()

@router.post("", dependencies=[Depends(get_current_user)])
async def create_or_update_account(data: AccountCreate):
    """
    新增或修改账号配置。
    保存时会默认进行双重验证：即测试代理连通性以及 X 登录凭证。
    """
    username = data.username.strip()
    if username.startswith("@"):
        username = username[1:]
        
    # 调用服务执行登录与代理校验
    success, msg = await test_account_and_proxy(
        username=username,
        password=data.password,
        email=data.email,
        proxy=data.proxy
    )
    
    if not success:
        # 如果测试不通过，直接报错抛给前端展示，不保存入库
        raise HTTPException(status_code=400, detail=msg)
        
    # 验证通过，入库保存
    add_account(
        username=username,
        password=data.password,
        email=data.email,
        proxy=data.proxy,
        remark=data.remark
    )
    return {"message": "账号及代理验证通过并保存成功"}

@router.delete("/{username}", dependencies=[Depends(get_current_user)])
async def remove_account(username: str):
    """
    从池子删除账号
    """
    delete_account(username)
    return {"message": "账号已删除"}
