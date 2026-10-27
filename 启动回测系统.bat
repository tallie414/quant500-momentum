@echo off
rem ===== Quant500 Momentum Web App Launcher =====
rem 双击即可启动；演示期间请保持本窗口打开，关闭窗口即停止服务。

title Quant500 Backtest System

rem ---- 清除代理环境变量（VPN 会破坏本地访问与国内数据源）----
set HTTP_PROXY=
set HTTPS_PROXY=
set http_proxy=
set https_proxy=
set ALL_PROXY=
set all_proxy=
set NO_PROXY=localhost,127.0.0.1

cd /d D:/quant500_momentum

set PY=C:/Users/38776/miniconda3/python.exe

rem ---- 1. 依赖检查 ----
%PY% -c "import streamlit, plotly, pandas" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 依赖缺失，请先执行： %PY% -m pip install -r requirements.txt
    pause
    exit /b 1
)

rem ---- 2. 产物自检（秒级，确保 output/ 齐全）----
echo [1/3] 正在自检回测产物...
%PY% tests\test_smoke.py
if errorlevel 1 (
    echo.
    echo [WARN] 自检存在失败项，页面可能缺少数据。可先按提示修复，或按任意键继续启动。
    pause
)

rem ---- 3. 数据预热（仅首次，约 1~2 分钟；之后启动秒开在线回测）----
if not exist "data\web_ctx.pkl" (
    echo [2/3] 首次启动：正在预热数据缓存（约 1~2 分钟，之后不再需要）...
    %PY% precompute_presets.py --ctx-only
) else (
    echo [2/3] 检测到预热缓存 data\web_ctx.pkl，跳过预热。
)

rem ---- 4. 选择端口（如 8501 已被上次未关闭的实例占用，自动顺延）----
set PORT=8501
netstat -ano | findstr ":8501 " | findstr LISTENING >nul && set PORT=8502
netstat -ano | findstr ":8502 " | findstr LISTENING >nul && set PORT=8503

rem ---- 5. 启动 Web 应用 ----
echo [3/3] 正在启动 Streamlit（端口 %PORT%），浏览器会自动打开 http://localhost:%PORT%
echo       演示期间请勿关闭本窗口；关闭即停止服务。
if not "%PORT%"=="8501" echo       （提示：8501 已被其他实例占用，本次改用 %PORT%；若浏览器打开的是旧页面，请关闭旧的黑窗口）
%PY% -m streamlit run app.py --server.port %PORT%

pause
