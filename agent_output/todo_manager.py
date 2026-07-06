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
    import sys
    if len(sys.argv) < 2:
        list_todos()
    elif sys.argv[1] == 'add' and len(sys.argv) > 2:
        add_todo(' '.join(sys.argv[2:]))
    elif sys.argv[1] == 'done' and len(sys.argv) > 2:
        complete_todo(int(sys.argv[2]))
    else:
        list_todos()
