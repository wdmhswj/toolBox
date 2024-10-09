#include "PasswordGenerator.h"
#include <iostream>

int main() {
    int passwordLength;
    std::cout << "Enter the desired password length: ";
    std::cin >> passwordLength;

    PasswordGenerator pg;
    
    std::cout << "Generated password: " << pg.generatePassword(passwordLength) << std::endl;

    return 0;
}