param (
    [string]$FolderPath = "C:\example\text_files",    # 默认文件夹路径
    [string]$HashAlgorithm = "SHA256",                # 默认哈希算法
    [string[]]$FileExtensions = @(".txt")             # 默认处理的文件扩展名，可以接收多个扩展名
)

# 检查文件夹路径是否存在
if (!(Test-Path -Path $FolderPath)) {
    Write-Output "文件夹路径 '$FolderPath' 不存在，请检查路径。"
    exit
}

# 确保所有扩展名都以点号开始
$FileExtensions = $FileExtensions | ForEach-Object { 
    if ($_ -notlike ".*") { ".$_" } else { $_ }
}

# 显示处理信息
Write-Output "开始处理文件夹: $FolderPath"
Write-Output "使用哈希算法: $HashAlgorithm"
Write-Output "处理的文件类型: $($FileExtensions -join ', ')"

# 构造文件过滤器
$filter = $FileExtensions | ForEach-Object { "*$_" }

# 遍历文件夹中的指定扩展名的文件
$files = Get-ChildItem -Path $FolderPath -File | Where-Object { 
    $file = $_
    $FileExtensions | Where-Object { $file.Name -like "*$_" }
}

if ($files.Count -eq 0) {
    Write-Output "没有找到符合条件的文件。"
    exit
}

$processedCount = 0
$skippedCount = 0
$errorCount = 0

foreach ($file in $files) {
    try {
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
            Copy-Item -Path $file.FullName -Destination $newFilePath -Force
            Write-Output "已创建新文件 '$newFileName' 复制自 '$($file.Name)'"
            $processedCount++
        }
        else {
            Write-Output "文件 '$newFileName' 已存在，跳过复制 '$($file.Name)'"
            $skippedCount++
        }
    }
    catch {
        Write-Error "处理文件 '$($file.Name)' 时出错: $_"
        $errorCount++
    }
}

# 显示处理结果统计
Write-Output "`n处理完成!"
Write-Output "成功处理文件数: $processedCount"
Write-Output "跳过的文件数: $skippedCount"
Write-Output "失败的文件数: $errorCount"