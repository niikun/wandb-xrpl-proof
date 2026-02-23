"""
pytest の conftest.py: .env ファイルを自動読み込みする。

python-dotenv を使い、リポジトリルートの .env から環境変数を読み込む。
.env が存在しない場合は何もしない (CI 環境では環境変数を直接注入)。
"""

from pathlib import Path

from dotenv import load_dotenv

# リポジトリルートの .env を読み込む (override=False: 既存の環境変数は上書きしない)
_env_file = Path(__file__).parent.parent / ".env"
load_dotenv(_env_file, override=False)
