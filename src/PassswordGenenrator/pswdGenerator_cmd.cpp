

#include <iostream>
#include <string>
#include "PasswordGenerator.h"
int main(int argc, char* argv[])
{
    if(argc!=2)
    {
        std::cerr << "Usage: " << argv[0] << " <password_length>" << std::endl;
        return 1;
    }

    int passwordLength = std::stoi(argv[1]);
    std::string password = PasswordGenerator::generatePassword(passwordLength);
    std::cout  << password << std::endl;
    return 0;
}