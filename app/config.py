"""設定。環境変数(.env)から読む。鍵の値はここに書かない。"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./local.db"
    # dev = 「dev:<uid>」というトークンを受け付ける(ローカルとテスト用)。firebase = Firebase の ID トークンを検証する(本番)
    auth_mode: str = "dev"
    firebase_project_id: str = "uttc-class10-react"
    # Gemini:API キー(Google AI Studio)か、Vertex AI(Cloud Run のサービスアカウント)のどちらか
    gemini_api_key: str = ""
    gemini_use_vertex: bool = False
    gemini_project: str = ""
    gemini_location: str = "global"
    gemini_model: str = "gemini-3.8-flash"
    cors_origins: str = "http://localhost:5173"
    # true のとき、起動時に商品が1件も無ければデモデータを入れる(Cloud SQL につなぐまでの仮の対応。コンテナ内の SQLite は再起動で消えるため)
    seed_on_empty: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
