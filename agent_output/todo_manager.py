def main():
    todos = []
    while True:
        print('\n1. Add 2. View 3. Remove 4. Exit')
        choice = input('Choice: ')
        if choice == '1':
            todos.append(input('Task: '))
        elif choice == '2':
            for i, t in enumerate(todos, 1): print(f'{i}. {t}')
        elif choice == '3':
            idx = int(input('Index: ')) - 1
            if 0 <= idx < len(todos): todos.pop(idx)
        elif choice == '4':
            break

if __name__ == '__main__':
    main()
