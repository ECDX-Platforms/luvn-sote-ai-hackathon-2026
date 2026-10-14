import { forwardRef, useId, type HTMLAttributes, type ReactNode } from 'react';
import { Checkbox, type CheckboxProps } from '../Checkbox';
import styles from './TaskItem.module.css';

export interface TaskListProps extends HTMLAttributes<HTMLUListElement> {
  /** The tasks: TaskItem elements, in order. */
  children: ReactNode;
}

/** List of task items (`<ul>`). Draws the hairline between items; the last item has none. */
export const TaskList = forwardRef<HTMLUListElement, TaskListProps>(function TaskList(
  { className, children, ...rest },
  ref,
) {
  return (
    <ul {...rest} ref={ref} className={[styles.list, className].filter(Boolean).join(' ')}>
      {children}
    </ul>
  );
});

export interface TaskItemProps extends Omit<CheckboxProps, 'children' | 'disabled'> {
  /** Figma: Checked (Off / On). Use `checked` + `onChange`, or `defaultChecked` for an uncontrolled task. */
  checked?: boolean;
  /** Figma: Checkbox → Label. The task title, in body. Names the checkbox. Wraps when long. */
  children: ReactNode;
  /** Figma: Meta. One line of meta under the title (due date, owner, time), caption in text-muted. Read as the checkbox's description. Wraps when long. */
  meta?: ReactNode;
}

/**
 * One task: checkbox, title, meta under it. Renders an `<li>`: put it in a TaskList.
 * The whole row is the checkbox's label, so a click anywhere on it toggles the task.
 * `className` and `style` go on the `<li>`; every other prop goes on the `<input>`. Styleguide: Checkbox and task item.
 */
export const TaskItem = forwardRef<HTMLInputElement, TaskItemProps>(function TaskItem(
  { meta, className, style, children, 'aria-describedby': describedBy, ...rest },
  ref,
) {
  const titleId = useId();
  const metaId = useId();
  const hasMeta = meta !== undefined && meta !== null && meta !== false && meta !== '';

  return (
    <li className={[styles.item, className].filter(Boolean).join(' ')} style={style}>
      <Checkbox
        {...rest}
        ref={ref}
        className={[styles.row, hasMeta ? styles.withMeta : null].filter(Boolean).join(' ')}
        aria-labelledby={titleId}
        aria-describedby={[hasMeta ? metaId : null, describedBy].filter(Boolean).join(' ') || undefined}
      >
        <span id={titleId} className={styles.title}>{children}</span>
        {hasMeta ? <span id={metaId} className={[styles.meta, 't-caption'].join(' ')}>{meta}</span> : null}
      </Checkbox>
    </li>
  );
});
