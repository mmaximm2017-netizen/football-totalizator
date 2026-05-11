from app import create_app

print("START APP")

app = create_app()

print("APP CREATED")

if __name__ == '__main__':
    print("RUN SERVER")
    app.run(debug=False, host='0.0.0.0', port=5000)