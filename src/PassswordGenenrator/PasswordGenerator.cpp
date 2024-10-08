// PasswordGenerator.cpp
#include "PasswordGenerator.h"
#include <cstdlib>
#include <ctime>

std::string PasswordGenerator::generatePassword(int length) {
    const std::string characters = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()_+";
    std::string password;

    std::srand(std::time(nullptr));

    for (int i = 0; i < length; ++i) {
        password += characters[std::rand() % characters.length()];
    }

    return password;
}
