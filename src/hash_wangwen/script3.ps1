# .\script.ps1 -FolderPath .\test\ -HashAlgorithm "MD5" -FileExtensions ".txt"
# .\script.ps1 -FolderPath .\test\ -HashAlgorithm "MD5" -FileExtensions @(".txt", ".doc") -LogFile "custom_log.txt"
# .\script.ps1 -FolderPath .\test\ -HashAlgorithm "MD5" -FileExtensions "txt","doc" -LogFile "C:\logs\hash_rename.log"

param (
    [string]$FolderPath = "C:\example\text_files",    # 默认文件夹路径
    [string]$HashAlgorithm = "SHA256",                # 默认哈希算法
    [string[]]$FileExtensions = @(".txt"),            # 默认处理的文件扩展名，可以接收多个扩展名
    [string]$LogFile = "FileHashRename.log"           # 默认日志文件名
)

# 函数：写入日志
function Write-Log {
    param(
        [string]$Message,
        [switch]$IsError
    )
    
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logMessage = "[$timestamp] $Message"
    
    # 同时输出到控制台和日志文件
    if ($IsError) {
        Write-Error $Message
        Add-Content -Path $LogFile -Value "[$timestamp] ERROR: $Message"
    } else {
        Write-Output $Message
        Add-Content -Path $LogFile -Value $logMessage
    }
}

try {
    # 检查并创建日志文件
    if (!(Test-Path -Path $LogFile)) {
        New-Item -Path $LogFile -ItemType File -Force | Out-Null
    }

    # 写入脚本启动信息
    Write-Log "====================== 脚本执行开始 ======================"
    Write-Log "脚本执行时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Log "处理文件夹: $FolderPath"
    Write-Log "使用哈希算法: $HashAlgorithm"

    # 检查文件夹路径是否存在
    if (!(Test-Path -Path $FolderPath)) {
        Write-Log "文件夹路径 '$FolderPath' 不存在，请检查路径。" -IsError
        exit
    }

    # 确保所有扩展名都以点号开始
    $FileExtensions = $FileExtensions | ForEach-Object { 
        if ($_ -notlike ".*") { ".$_" } else { $_ }
    }
    Write-Log "处理的文件类型: $($FileExtensions -join ', ')"

    # 获取符合条件的文件
    $files = Get-ChildItem -Path $FolderPath -File | Where-Object { 
        $file = $_
        $FileExtensions | Where-Object { $file.Name -like "*$_" }
    }

    if ($files.Count -eq 0) {
        Write-Log "没有找到符合条件的文件。"
        exit
    }

    Write-Log "找到 $($files.Count) 个符合条件的文件"

    $processedCount = 0
    $skippedCount = 0
    $errorCount = 0

    # 创建一个哈希表来存储文件映射关系
    $fileMapping = @{}

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
            
            # 记录文件映射关系
            $fileMapping[$file.Name] = $newFileName

            # 如果新文件名不存在，则创建新文件；否则跳过以避免覆盖
            if (!(Test-Path -Path $newFilePath)) {
                Copy-Item -Path $file.FullName -Destination $newFilePath -Force
                Write-Log "已创建新文件 '$newFileName' 复制自 '$($file.Name)'"
                $processedCount++
            }
            else {
                Write-Log "文件 '$newFileName' 已存在，跳过复制 '$($file.Name)'"
                $skippedCount++
            }
        }
        catch {
            Write-Log "处理文件 '$($file.Name)' 时出错: $_" -IsError
            $errorCount++
        }
    }

    # 生成文件映射报告
    Write-Log "`n文件映射关系:"
    Write-Log "------------------------"
    $fileMapping.GetEnumerator() | ForEach-Object {
        Write-Log "原文件: $($_.Key)"
        Write-Log "新文件: $($_.Value)"
        Write-Log "------------------------"
    }

    # 显示处理结果统计
    Write-Log "`n处理完成!"
    Write-Log "成功处理文件数: $processedCount"
    Write-Log "跳过的文件数: $skippedCount"
    Write-Log "失败的文件数: $errorCount"
    Write-Log "====================== 脚本执行结束 ======================"
}
catch {
    Write-Log "脚本执行过程中发生错误: $_" -IsError
}
finally {
    Write-Log "日志文件位置: $((Get-Item $LogFile).FullName)"
}