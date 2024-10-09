
import pandas as pd

# 读取 .xls 文件
file_path = 'test.xls'
df = pd.read_excel(file_path)

# 显示数据
# print(df)

# 提取第一列（学号）
student_ids = df.iloc[:, 0]
# print(student_ids)
# 将浮点类型转换为整数类型（去掉小数部分）
# 首先去掉空值（如果有），然后转换为整数类型
student_ids = student_ids.fillna(0).astype(int)

# 输出所有学号
print(student_ids)

# import requests

# # 需要访问的目标 URL（假设需要登录的页面）
# protected_url = 'https://zhcp.seiee.sjtu.edu.cn/creditManagement/Rank'

# # 从浏览器获取的 Cookies，格式为键值对
# cookies = {
#     # 添加其他所需的 cookies
#     '_ga': 'GA1.3.1429017764.1694413226',
#     '_ga_QP6YR9D8CK': 'GS1.3.1713318089.47.0.1713318089.0.0.0',
#     'Hm_lvt_b70cf0db803b019005958f43df9d261b': '1724582368,1724585029,1725946591,1726298058',
# }

# # 发送带有 cookies 的请求
# response = requests.get(protected_url, cookies=cookies)

# # 检查请求是否成功
# if response.ok:
#     print("访问成功！")
#     print(response.text)  # 输出页面内容
# else:
#     print("访问失败。")


import requests

# 目标 URL
# url = 'https://zhcp.seiee.sjtu.edu.cn/api/zhcp/achievements/students/522031910105'
url = 'https://zhcp.seiee.sjtu.edu.cn/api/zhcp/achievements/students/'

# 自定义 headers
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Encoding': 'gzip, deflate, br, zstd',
    'Connection': 'keep-alive',
    'Authorization': '13a7f4662c02797eeb5365870914ef09',  # 如果有需要授权的情况
}

# # 发起 GET 请求，并将 headers 传递进去
# response = requests.get(url, headers=headers)

# # 检查响应
# if response.ok:
#     print("请求成功")
#     # print(response.text)
#     rp_json = response.json()
#     name = rp_json['result'][0]['name']
#     class_ = rp_json['result'][0]['class']
#     print(name, class_)
# else:
#     print("请求失败，状态码:", response.status_code)


# Step 1: Initialize an empty list to store the student info
students = []

# Function to add a student's information (ID, Name, Grade)
def add_student_info(student_id, name, class_):
    student_info = {
        "ID": student_id,
        "Name": name,
        "Class": class_
    }
    students.append(student_info)


# 以写入模式打开文件
with open('output2.txt', 'w') as f:
    print("这将被写入到文件中。", file=f)
    print("另一行。", file=f)
    for id in student_ids:
        print('------',id,'------')
        print('------',id,'------', file=f)
        get_url = url + str(id)
        print("get: "+get_url)
        print("get: "+get_url, file=f)
        # 发起 GET 请求，并将 headers 传递进去
        response = requests.get(get_url, headers=headers)
        # 检查响应
        if response.ok:
            print("请求成功")
            print("请求成功", file=f)
            # print(response.text)
            rp_json = response.json()
            # name = rp_json['result'][0]['name']
            # class_ = rp_json['result'][0]['class']

            # try:
            #     # 尝试获取 class 字段
            #     name = rp_json['result'][0]['name']
            #     class_ = rp_json['result'][0]['class']
            # except KeyError:
            #     # 如果 class 字段不存在，执行以下代码
            #     name = ''
            #     class_ = None  # 或者你可以设置一个默认值
            # except Exception as e:  # 捕获所有异常
            #     name = ''
            #     class_ = None
            #     print(f"发生错误: {e}")  # 输出错误信息
            #     print(f"发生错误: {e}", file=f)  # 输出错误信息

            try:
                # 确保 'result' 是一个非空列表
                if rp_json['result']:  # 先检查是否有结果
                    name = rp_json['result'][0]['name']
                    class_ = rp_json['result'][0]['class']
                else:
                    name = ''
                    class_ = None  # 没有结果时设置默认值
            except KeyError as e:
                # 捕获 KeyError
                name = ''
                class_ = None  # 设置默认值
                print(f"发生 KeyError: {e}")  # 输出错误信息
                print(f"发生 KeyError: {e}", file=f)  # 输出错误信息到文件
            except IndexError as e:
                # 捕获 IndexError
                name = ''
                class_ = None  # 设置默认值
                print(f"发生 IndexError: {e}")  # 输出错误信息
                print(f"发生 IndexError: {e}", file=f)  # 输出错误信息到文件
            except Exception as e:
                # 捕获其他异常
                name = ''
                class_ = None  # 设置默认值
                print(f"发生其他错误: {e}")  # 输出错误信息
                print(f"发生其他错误: {e}", file=f)  # 输出错误信息到文件

            # 继续后续处理

                        
            print(id,name, class_)
            print(id,name, class_, file=f)
            add_student_info(id, name, class_)

        
        else:
            print("请求失败，状态码:", response.status_code)
            print("请求失败，状态码:", response.status_code, file=f)
            break


# for id in student_ids:
#     print('------',id,'------')
#     get_url = url + str(id)
#     print("get: "+get_url)
#     # 发起 GET 请求，并将 headers 传递进去
#     response = requests.get(get_url, headers=headers)
#     # 检查响应
#     if response.ok:
#         print("请求成功")
#         # print(response.text)
#         rp_json = response.json()
#         name = rp_json['result'][0]['name']
#         # class_ = rp_json['result'][0]['class']

#         try:
#             # 尝试获取 class 字段
#             name = rp_json['result'][0]['name']
#             class_ = rp_json['result'][0]['class']
#         except KeyError:
#             # 如果 class 字段不存在，执行以下代码
#             name = ''
#             class_ = None  # 或者你可以设置一个默认值
        
#         print(id,name, class_)
#         add_student_info(id, name, class_)

    
#     else:
#         print("请求失败，状态码:", response.status_code)
#         break

# Step 3: Save the data to an Excel file
df = pd.DataFrame(students)
df.to_excel("students_info.xlsx", index=False)