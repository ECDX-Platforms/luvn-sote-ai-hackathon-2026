import { forwardRef, useId, useState, type FormEvent, type ReactNode } from 'react';
import { Button } from '../Button';
import { Dialog, type DialogProps } from '../Dialog';
import { Field } from '../Field';
import { Input } from '../Input';
import { Tooltip } from '../Tooltip';
import styles from './TypedConfirmation.module.css';

export interface TypedConfirmationProps extends Omit<DialogProps, 'children' | 'actions' | 'closeOnEscape' | 'size'> {
  /** Figma: Title. The risky action and what it applies to ("Delete Archive-2024?"). */
  title: string;
  /** Figma: Body. What will happen, to what, and whether it can be undone. */
  description: ReactNode;
  /** The word or name the user must type, e.g. the item's name. The danger button turns on when the typed text matches. */
  confirmText: string;
  /** Figma: Field label. Sentence case, no colon. */
  fieldLabel?: string;
  /** Figma: Field hint. Shows the text to type. Default "Name: <confirmText>". */
  hint?: string;
  /** The danger button's label: verb + object ("Delete folder"). */
  confirmLabel: string;
  /** The secondary button's label. */
  cancelLabel?: string;
  /** Why the danger button is still off. Shown in its tooltip. Default "Type <confirmText> first." */
  disabledReason?: string;
  /** Called when the text matches and the user presses the danger button or Enter in the field. Close the dialog here too. */
  onConfirm: () => void;
  /** Text in the field when the dialog opens. For docs and tests only; leave it out in product code. */
  defaultValue?: string;
}

/**
 * Typed confirmation (styleguide rule 11): a Dialog for risky actions. It says what will happen, asks the user to
 * type a given word, and the danger button stays disabled until the text matches. Esc does not close it; Cancel does.
 */
export const TypedConfirmation = forwardRef<HTMLDialogElement, TypedConfirmationProps>(function TypedConfirmation(
  {
    open,
    onClose,
    title,
    description,
    confirmText,
    fieldLabel = 'Type the name to confirm',
    hint,
    confirmLabel,
    cancelLabel = 'Cancel',
    disabledReason,
    onConfirm,
    defaultValue = '',
    'aria-describedby': describedBy,
    ...rest
  },
  ref,
) {
  const formId = useId();
  const descriptionId = useId();
  const [value, setValue] = useState(defaultValue);
  const [wasOpen, setWasOpen] = useState(open);

  // Start empty every time it opens: a match from an earlier attempt must not carry over.
  // Reset while rendering (not in an effect), so the old text never shows, not even for one frame.
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setValue(defaultValue);
  }

  // Spaces around the text and letter case do not count; every other character must match.
  // An empty word to type never matches: the danger button must not turn on by itself.
  const matches =
    confirmText.trim() !== '' && value.trim().localeCompare(confirmText.trim(), undefined, { sensitivity: 'accent' }) === 0;

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (matches) onConfirm();
  };

  const confirmButton = (
    <Button variant="danger" type="submit" form={formId} disabled={!matches}>
      {confirmLabel}
    </Button>
  );

  return (
    <Dialog
      {...rest}
      ref={ref}
      open={open}
      onClose={onClose}
      title={title}
      size="sm"
      closeOnEscape={false}
      aria-describedby={[descriptionId, describedBy].filter(Boolean).join(' ')}
      actions={
        <>
          <Button onClick={onClose}>{cancelLabel}</Button>
          {matches ? confirmButton : <Tooltip text={disabledReason ?? `Type ${confirmText} first.`}>{confirmButton}</Tooltip>}
        </>
      }
    >
      <form id={formId} className={styles.form} onSubmit={handleSubmit} noValidate>
        <div id={descriptionId}>{description}</div>
        <Field label={fieldLabel} hint={hint ?? `Name: ${confirmText}`}>
          <Input
            value={value}
            onChange={(event) => setValue(event.target.value)}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
          />
        </Field>
      </form>
    </Dialog>
  );
});
