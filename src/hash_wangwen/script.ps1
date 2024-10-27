param (
    [string]$FolderPath = "C:\example\text_files",    # 默认文件夹路径
    [string]$HashAlgorithm = "SHA256"                 # 默认哈希算法
)

# 检查文件夹路径是否存在
if (!(Test-Path -Path $FolderPath)) {
    Write-Output "文件夹路径 '$FolderPath' 不存在，请检查路径。"
    exit
}

# 遍历文件夹中的所有 .txt 文件
Get-ChildItem -Path $FolderPath -Filter "*.txt" | ForEach-Object {
    $file = $_
    
    # 读取文件内容并计算哈希
    $hash = (Get-FileHash -Path $file.FullName -Algorithm $HashAlgorithm).Hash
    
    # 去除哈希值中的冒号，以避免文件名语法问题
    $cleanHash = $hash -replace ":", ""
    
    # 构造新的文件名，使用原始扩展名
    $newFileName = "$cleanHash$($file.Extension)"
    
    # 构造完整的新文件路径
    $newFilePath = Join-Path -Path $FolderPath -ChildPath $newFileName
    
    # 如果新文件名不存在，则创建新文件；否则跳过以避免覆盖
    if (!(Test-Path -Path $newFilePath)) {
        try {
            # 复制原文件内容到新文件
            Copy-Item -Path $file.FullName -Destination $newFilePath -Force
            Write-Output "已创建新文件 '$newFileName' 复制自 '$($file.Name)'"
        }
        catch {
            Write-Error "复制文件时出错: $_"
        }
    }
    else {
        Write-Output "文件 '$newFileName' 已存在，跳过复制 '$($file.Name)'"
    }
}