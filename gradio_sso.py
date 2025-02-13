# -*- coding: utf-8 -*-
"""
Created on Wed Nov 20 08:24:18 2024

@author: pc
"""

import os
from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import FastAPI, Depends, Request, HTTPException
from starlette.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
import uvicorn
import gradio as gr
import httpx
import json
from dataclasses import dataclass, field, asdict

from dotenv import load_dotenv
load_dotenv()


@dataclass
class IhepSSOConfig:
    name: str = "ihepsso"
    client_id: str = field(default_factory=lambda: os.getenv("IHEP_SSO_APP_KEY"))
    client_secret: str = field(default_factory=lambda: os.getenv("IHEP_SSO_APP_SECRET"))
    redirect_uri: str = field(default_factory=lambda: os.getenv("IHEP_SSO_REDIRECT_URI"))

    authorize_url: str = field(default="https://login.ihep.ac.cn/oauth2/authorize", metadata={"description": "ihep oauth authorize_url"})
    access_token_url: str = field(default="https://login.ihep.ac.cn/oauth2/token", metadata={"description": "ihep oauth access_token_url"})
    theme: str = field(default="full", metadata={"description": "ihep oauth theme"})


    api_base_url: str = field(default=None, init=False)
    meddleware_secret: str = field(default=None, init=False)

    def __post_init__(self):
        if not self.client_id:
            raise ValueError("IHEP_SSO_APP_KEY is not set, please set it in .env file")
        if not self.client_secret:
            raise ValueError("IHEP_SSO_APP_SECRET is not set, please set it in .env file")
        if not self.redirect_uri:
            raise ValueError("IHEP_SSO_REDIRECT_URI is not set, please set it in .env file")

        self.api_base_url = self.redirect_uri.split("/umt")[0]
        self.meddleware_secret = self.get_middleware_secret()

    @classmethod
    def get_middleware_secret(cls):
        def _random_secret():
            import secrets
            return secrets.token_urlsafe(32)
        secreate = os.environ.get('SECRET_KEY') or _random_secret()
        return secreate
    

@dataclass
class UserInfo:
    username: str = field(default=None, metadata={"description": "用户姓名"})
    email: str = field(default=None, metadata={"description": "用户邮箱"})
    type: str = field(default=None, metadata={"description": "用户类型, inside"})
    umt_id: str = field(default=None, metadata={"description": "用户的统一认证ID"})

    def to_dict(self):
        return asdict(self)

## 配置OAuth2客户端 ## 
oauth = OAuth()
oauth_config = IhepSSOConfig()

oauth.register(
    name=oauth_config.name,
    client_id=oauth_config.client_id,
    client_secret=oauth_config.client_secret,
    access_token_url=oauth_config.access_token_url,
    authorize_url=oauth_config.authorize_url,
    # api_base_url=oauth_config.api_base_url,
    client_kwargs={
        "theme": oauth_config.theme,
        "redirect_uri": oauth_config.redirect_uri,
    },
)

## 配置fastapi app ##
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=oauth_config.meddleware_secret)

def get_user(request: Request):
    user = request.session.get('user')
    if user:
        return user
    return None

@app.get('/')
def index(user: dict = Depends(get_user)):
    if user:  # ④ 登录后，有用户信息，跳转到主界面
        return RedirectResponse(url='/main')
    else: # 如果没有登录，跳转到统一认证登录页面
        return RedirectResponse(url='/sso-login')

@app.route('/umt/logout')
async def logout(request: Request):
    # ⑥ 点击登出按钮，清除session的user
    request.session.pop('user', None)
    return RedirectResponse(url='/')

@app.route('/umt/callback')
async def auth(request: Request):
    # ③ 统一认证回调
    code = request.query_params.get('code')
    if code is None:
        error = request.query_params.get('error_description')
        raise HTTPException(status_code=400, detail=f"Failed to fetch code, error: {error}")
    payload = { 
        "client_id": oauth_config.client_id,
        "client_secret": oauth_config.client_secret,
        "grant_type":"authorization_code",
        "redirect_uri": oauth_config.redirect_uri,
        "code":code
        }
    async with httpx.AsyncClient() as client:
            response = await client.post(
                oauth_config.access_token_url,
                data=payload
            )
    response.raise_for_status()
    jsonInfo = response.json()
    
    userdata = json.loads(jsonInfo.get("userInfo"))
    user_info = UserInfo(
        username=userdata["truename"].strip(),
        email=userdata['cstnetId'],
        type=userdata['type'],
        umt_id=userdata['umtId']
        )
    print(f'SSO authed user: {user_info}')

    request.session['user'] = user_info.to_dict()  # 把用户信息保存到session中

    return RedirectResponse(url='/')  # 回调首页，可以自己改回调页面

@app.route('/umt/login')
async def login(request: Request):
    # ② 跳转到统一认证登录页面
    redirect_uri = oauth_config.redirect_uri
    authorize_redirect = await oauth.ihepsso.authorize_redirect(request, redirect_uri)
    return authorize_redirect

## Gradio部分 ##
with gr.Blocks() as login_demo:  # ① 显示登录按钮
    gr.Button("IHEP SSO Login", link="/umt/login")

def greet(request: gr.Request):
    current_user = UserInfo(**request.username)
    return f"Welcome to Gradio! **{current_user.username}**"

with gr.Blocks() as main_blocks:  # ⑤ 显示主界面
    m = gr.Markdown("Welcome to Gradio!")
    gr.Button("Logout", link="/umt/logout")
    main_blocks.load(greet, None, m)

## 挂载Gradio到FastAPI中 ##
app = gr.mount_gradio_app(app, login_demo, path="/sso-login") # 挂载gradio
app = gr.mount_gradio_app(app, main_blocks, path="/main", auth_dependency=get_user)

if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=7860) # 修改自己的ip地址和端口