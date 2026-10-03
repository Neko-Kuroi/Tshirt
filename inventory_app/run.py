from app import create_app

app = create_app()

if __name__ == "__main__":
    # 開発用。本番は gunicorn / waitress を使う(README参照)
    app.run(host="127.0.0.1", port=5000, debug=True)
