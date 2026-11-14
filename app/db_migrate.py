"""DB のマイグレーション(Alembic)を起動時に実行する。

これまでは起動時に「無いテーブルだけ作る(create_all)」だけで、列の追加や変更を追えなかった。
Alembic は「この DB はどこまで変更を適用したか」を alembic_version テーブルに覚えておき、まだの分だけを順に実行する。
変更の中身は migrations/versions/ のファイル(1ファイル=1回の変更)。
"""
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, inspect, text

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
# baseline(0001)が作るテーブル。「Alembic を入れる前から動いていた DB か」の判定に使う。
# 今後モデルが増えても、ここは 0001 の時点のまま変えない(Base.metadata を見ると、新しいテーブルが増えたときに判定が狂う)
BASELINE_TABLES = {"users", "items", "orders", "offers", "likes"}
BASELINE_REVISION = "0001"
_LOCK_NAME, _LOCK_WAIT_SECONDS = "nego_migrations", 60


def _config(conn: Connection) -> Config:
    """Alembic の設定をプログラムから作る。alembic.ini は読まない。
    なぜ:DB の URL を ini(configparser)に通すと、URL に含まれる % が補間の記号として解釈されて壊れる。
    URL を渡す代わりに、アプリが開いた接続そのものを渡す(migrations/env.py が受け取る)。"""
    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "migrations").replace("%", "%%"))
    cfg.attributes["connection"] = conn
    return cfg


def run_migrations(engine: Engine) -> str:
    """起動時に呼ぶ。返り値は何をしたか("stamp" または "upgrade")。失敗したら例外(起動を止める)。

    (a) alembic_version が無いのにアプリのテーブルはある = Alembic を入れる前から動いていた DB(導入時点の本番)
        → テーブルには触らず、「baseline まで適用済み」の印だけを付ける(stamp)。
          そのあと upgrade head を続ける。baseline より後のマイグレーションが将来増えていても、その分だけが実行される。
    (b) それ以外(空の DB、または印が付いている DB)→ upgrade head(まだの分だけ実行。最新なら何もしない)。
    """
    with engine.connect() as conn:
        locked = _lock(conn)
        try:
            with conn.begin():
                tables = set(inspect(conn).get_table_names())
                cfg = _config(conn)
                if "alembic_version" not in tables and tables & BASELINE_TABLES:
                    missing = BASELINE_TABLES - tables
                    if missing:  # 一部だけある状態は想定外。印を付けると足りないテーブルが永久に作られないので、止めて人が見る
                        raise RuntimeError(f"DB にアプリのテーブルが一部しかありません(無い: {sorted(missing)})。手で確認してください")
                    log.info("migrations: existing tables without alembic_version -> stamp %s", BASELINE_REVISION)
                    command.stamp(cfg, BASELINE_REVISION)
                    action = "stamp"
                else:
                    action = "upgrade"
                command.upgrade(cfg, "head")
            log.info("migrations: done (%s)", action)
            return action
        finally:
            if locked:
                _unlock(conn)


def _lock(conn: Connection) -> bool:
    """MySQL のとき、名前付きロックを取る。Cloud Run が複数のインスタンスを同時に起動しても、マイグレーションを実行するのは1つずつにするため。
    SQLite(ローカル・テスト)は1プロセスなので何もしない。"""
    if conn.dialect.name != "mysql":
        return False
    got = conn.execute(text("SELECT GET_LOCK(:n, :t)"), {"n": _LOCK_NAME, "t": _LOCK_WAIT_SECONDS}).scalar()
    conn.commit()  # SELECT で始まったトランザクションを閉じる(ロックは接続に付くので残る)
    if got != 1:
        raise RuntimeError("マイグレーションのロックを取れませんでした(ほかのインスタンスが実行中のまま終わらない)")
    return True


def _unlock(conn: Connection) -> None:
    try:
        conn.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": _LOCK_NAME})
        conn.commit()
    except Exception as e:  # 接続を閉じればロックは自動で外れるので、起動は止めない
        log.warning("migrations: release lock failed: %r", e)
        conn.invalidate()  # ロックを持ったままの接続をプールに戻さない(捨てれば MySQL 側でロックが外れる)
