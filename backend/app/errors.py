"""Safe domain errors, suitable for presentation to API consumers."""


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class AuthenticationError(AppError):
    def __init__(self):
        super().__init__(
            "AUTHENTICATION_FAILED",
            "Autentificare eșuată: parola este greșită sau datele au fost modificate.",
            400,
        )

