import json
import os

FILE_NAME = 'todos.json'

def load_todos():
    if os.path.exists(FILE_NAME):
        with open(FILE_NAME, 'r') as f:
            return json.load(f)
    return []

def save_todos(todos):
    with open(FILE_NAME, 'w') as f:
        json.dump(todos, f, indent=4)

def add_todo(task):
    todos = load_todos()
    todos.append({'task': task, 'done': False})
    save_todos(todos)
    print(f'Added: {task}')

def list_todos():
    todos = load_todos()
    if not todos:
        print('No todos found.')
        return
    for i, todo in enumerate(todos):
        status = '[x]' if todo['done'] else '[ ]'
        print(f'{i}. {status} {todo["task"]}')

def complete_todo(index):
    todos = load_todos()
    if 0 <= index < len(todos):
        todos[index]['done'] = True
        save_todos(todos)
        print(f'Completed: {todos[index]["task"]}')
    else:
        print('Invalid index.')

if __name__ == '__main__':
    while True:
        print('\n1. Add 2. List 3. Complete 4. Exit')
        choice = input('Choice: ')
        if choice == '1':
            add_todo(input('Task: '))
        elif choice == '2':
            list_todos()
        elif choice == '3':
            try:
                complete_todo(int(input('Index: ')))
            except ValueError:
                print('Please enter a valid number.')
        elif choice == '4':
            break
