# def open_devops_notes():
#     '''Opens the file named "DevOps Notes | (desktop automation)" using file_agent'''
#     try:
#         from agents.execution_agent.RAG.file_agent import find_file, open_file
#     except ImportError:
#         from file_agent import find_file, open_file
    
#     try:
#         result = find_file("Expense Exercise1")
        
#         if result["status"] == "found":
#             success = open_file(result["path"])
#             if success:
#                 return True
#         elif result["status"] == "multiple":
#             # Use first match
#             success = open_file(result["paths"][0]["path"])
#             if success:
#                 return True
#         else:
#             # Try fuzzy search with just "DevOps Notes"
#             result = find_file("python agent test")
#             if result["status"] == "found":
#                 success = open_file(result["path"])
#                 if success:
#                     return True
#             elif result["status"] == "multiple":
#                 success = open_file(result["paths"][0]["path"])
#                 if success:
#                     return True
        
#         return False
#     except Exception as e:
#         print(f"Error: {e}")
#         return False

# if __name__ == "__main__":
#     success = open_devops_notes()
#     if success:
#         print("EXECUTION_SUCCESS")
#     else:
#         print("EXECUTION_FAILED")


def launch_word():
    '''Launch Microsoft Word application'''
    try:
        doc_launch()
        return True
    except Exception as e:
        print(f"Error: {e}")
        return False

if __name__ == "__main__":
    success = launch_word()
    if success:
        print("EXECUTION_SUCCESS")
    else:
        print("EXECUTION_FAILED")

