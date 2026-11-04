@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 exit /b 1
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if "%~1"=="" goto menu
if /i "%~1"=="run" goto direct_run
if /i "%~1"=="test" goto direct_test
if /i "%~1"=="export" goto direct_export
if /i "%~1"=="setup" goto direct_setup
if /i "%~1"=="analyze" goto direct_analyze
echo 사용법: launcher.bat [run^|test^|export^|setup^|analyze]
set "RESULT=2"
goto finish

:menu
echo.
echo ==============================================
echo              명지레이더 수집기
echo ==============================================
echo  1. 게시판별 최대 3건 수집 - 빠른 확인
echo  2. 게시판별 최대 10건 수집
echo  3. 수집 결과 JSON을 메모장으로 열기
echo  4. 수집·분류·추출 검증 테스트 실행
echo  5. 실행 환경과 필수 라이브러리 설치·복구
echo  6. 저장된 공지 분류·정보 추출 - AI 없이 실행
echo  7. 분석 결과와 원문 근거를 메모장으로 열기
echo  0. 종료
echo.
choice /c 12345670 /n /m "실행할 번호를 선택하세요 [1/2/3/4/5/6/7/0]: "
if errorlevel 8 goto menu_exit
if errorlevel 7 goto menu_review
if errorlevel 6 goto menu_analyze
if errorlevel 5 goto menu_setup
if errorlevel 4 goto menu_test
if errorlevel 3 goto menu_open
if errorlevel 2 goto menu_run_ten
if errorlevel 1 goto menu_run
goto menu_exit

:menu_run
call :run 3
goto menu_result
:menu_run_ten
call :run 10
goto menu_result
:menu_test
call :test
goto menu_result
:menu_setup
call :setup
goto menu_result
:menu_analyze
call :analyze
goto menu_result
:menu_review
if not exist "data\analysis_review.md" (
    echo 분석 결과가 없습니다. 먼저 6번으로 공지를 분석해주세요.
) else (
    start "" notepad.exe "%CD%\data\analysis_review.md"
)
goto menu
:menu_open
if not exist "data\notices.json" (
    echo 수집 결과가 없습니다. 먼저 1번으로 공지를 수집해주세요.
) else (
    start "" notepad.exe "%CD%\data\notices.json"
)
goto menu
:menu_result
if errorlevel 1 (
    echo.
    echo 작업의 일부 또는 전체가 실패했습니다. 위의 오류 내용을 확인해주세요.
    echo 기존 수집 데이터는 유지됩니다.
) else (
    echo.
    echo 작업이 정상적으로 완료되었습니다.
)
echo 아무 키나 누르면 메뉴로 돌아갑니다.
pause >nul
goto menu
:menu_exit
set "RESULT=0"
goto finish

:direct_run
call :run 50
set "RESULT=%ERRORLEVEL%"
goto finish
:direct_test
call :test
set "RESULT=%ERRORLEVEL%"
goto finish
:direct_export
call :check_environment
if errorlevel 1 (
    set "RESULT=1"
    goto finish
)
".venv\Scripts\python.exe" -m collector export
set "RESULT=%ERRORLEVEL%"
goto finish
:direct_setup
call :setup
set "RESULT=%ERRORLEVEL%"
goto finish
:direct_analyze
call :analyze
set "RESULT=%ERRORLEVEL%"
goto finish

:check_environment
if not exist ".venv\Scripts\python.exe" (
    echo 파이썬 실행 환경이 없습니다. 메뉴 5번을 선택하거나 launcher.bat setup을 실행해주세요.
    exit /b 1
)
".venv\Scripts\python.exe" -c "import requests, bs4, tomllib" >nul 2>&1
if errorlevel 1 (
    echo 필수 라이브러리가 없거나 파이썬 버전이 낮습니다. Python 3.11 이상이 필요합니다.
    echo 메뉴 5번을 선택하거나 launcher.bat setup을 실행해주세요.
    exit /b 1
)
exit /b 0

:run
call :check_environment
if errorlevel 1 exit /b 1
echo 각 게시판에서 공지를 최대 %~1건씩 수집합니다. 인터넷 연결이 필요합니다.
echo 잠시 기다려주세요. 수집 중에는 다른 수집기를 동시에 실행하지 마세요.
".venv\Scripts\python.exe" -m collector run --limit %~1
exit /b %ERRORLEVEL%

:test
call :check_environment
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m unittest discover -s tests -v
exit /b %ERRORLEVEL%

:analyze
call :check_environment
if errorlevel 1 exit /b 1
echo 저장된 본문 텍스트를 규칙으로 분류하고 정보를 추출합니다.
echo 이미지와 첨부파일의 내용은 원문에서 직접 확인해야 합니다.
".venv\Scripts\python.exe" -m collector analyze
exit /b %ERRORLEVEL%

:setup
if exist ".venv\Scripts\python.exe" goto install_dependencies
python -c "import tomllib" >nul 2>&1
if not errorlevel 1 (
    python -m venv .venv
    if errorlevel 1 exit /b 1
    goto install_dependencies
)
py -3 -c "import tomllib" >nul 2>&1
if errorlevel 1 (
    echo Python 3.11 이상을 먼저 설치해주세요.
    echo 설치할 때 파이썬 실행기 또는 PATH 등록 옵션을 활성화해주세요.
    exit /b 1
)
py -3 -m venv .venv
if errorlevel 1 exit /b 1
:install_dependencies
".venv\Scripts\python.exe" -m pip install -r requirements.lock.txt
exit /b %ERRORLEVEL%

:finish
popd
exit /b %RESULT%
